"""
main.py — Entry point for the ICP Search Engine.

Usage:
    python main.py                          # uses companies.txt
    python main.py --company "Moodbit"      # single company
    python main.py --companies "A,B,C"      # comma-separated list
    python main.py --file my_list.txt       # custom file
    python main.py --backend playwright     # override search backend
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import nest_asyncio
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

import config
from csv_writer import ResultsWriter
from graph import run_company
from schemas import AgentState

# Fix nested event loops (needed in some environments)
nest_asyncio.apply()

logger = logging.getLogger(__name__)
console = Console()


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True, markup=True),
        ],
    )
    # Quieten noisy libraries
    for noisy in ("httpx", "httpcore", "openai", "langchain", "playwright"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Company list loading ──────────────────────────────────────────────────────

def load_companies(file_path: Path) -> list[str]:
    """Read company names from a .txt file (one per line, # = comment)."""
    if not file_path.exists():
        console.print(f"[red]File not found:[/red] {file_path}")
        sys.exit(1)

    companies = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            companies.append(line)

    if not companies:
        console.print("[red]No companies found in file. Check for empty lines or all comments.[/red]")
        sys.exit(1)

    return companies


def deduplicate_companies(companies: list[str]) -> list[str]:
    """
    Remove duplicate company names while preserving the original order.

    Uses dict.fromkeys() which is O(n) and guarantees insertion-order
    deduplication (Python 3.7+). Comparison is case-sensitive — 'Asana'
    and 'asana' are treated as distinct inputs.
    """
    deduplicated = list(dict.fromkeys(companies))
    dropped = len(companies) - len(deduplicated)
    if dropped > 0:
        logger.warning(
            f"Input deduplication: removed {dropped} duplicate "
            f"entr{'y' if dropped == 1 else 'ies'} from the company list."
        )
    return deduplicated


# ── Semaphore-controlled concurrent runner ────────────────────────────────────

async def run_all(
    companies: list[str],
    writer: ResultsWriter,
    max_concurrent: int,
) -> list[AgentState]:
    """
    Run the pipeline for all companies with controlled concurrency.
    Uses a semaphore to limit parallel executions, then writes results
    to CSV as each company completes (incremental output).
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    write_lock = asyncio.Lock()
    results: list[AgentState] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Processing companies…", total=len(companies))

        async def process_one(company: str) -> AgentState:
            async with semaphore:
                state = await run_company(company)
                async with write_lock:
                    writer.write_company(state)
                progress.advance(task_id)
                return state

        results = await asyncio.gather(
            *[process_one(c) for c in companies],
            return_exceptions=False,
        )

    return list(results)


# ── Summary table ─────────────────────────────────────────────────────────────

def print_summary(results: list[AgentState]):
    table = Table(title="🎯 ICP Search Results", show_lines=True)
    table.add_column("Company", style="bold cyan", no_wrap=True)
    table.add_column("ICP Score", justify="center", style="yellow")
    table.add_column("Action", style="bold")
    table.add_column("Creative Roles", justify="center", style="green")
    table.add_column("Tech Stack", style="magenta")
    table.add_column("MI Signals", justify="center", style="cyan")

    for state in results:
        p = state.profile
        sigs = p.automotive_signals
        
        # ICP Score
        icp_score_str = str(p.icp_score) if p.icp_score > 0 else "—"
        
        # Action
        action = p.recommended_action or "—"
        
        # Creative Roles (from Signal 5 / Phase 4.3)
        if sigs.is_hiring_creative_roles:
            roles_cnt = len(sigs.creative_job_titles)
            roles_str = f"Yes ({roles_cnt})" if roles_cnt > 0 else "Yes"
        else:
            roles_str = "No"

        # Tech Stack (truncate to first 2-3 tools)
        tools = sigs.detected_creative_tools
        if tools:
            tech_str = ", ".join(tools[:3])
            if len(tools) > 3:
                tech_str += "..."
        else:
            tech_str = "—"

        # MI Signals
        mi_str = "Yes" if sigs.has_michigan_local_involvement else "No"

        table.add_row(
            p.company_name,
            icp_score_str,
            action,
            roles_str,
            tech_str,
            mi_str,
        )

    console.print(table)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ICP Search Engine — Research any company's profile and growth signals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --company "Moodbit"
  python main.py --companies "Salesforce,HubSpot,Notion"
  python main.py --file my_leads.txt --backend serper
  python main.py --company "OpenAI" --backend playwright
        """,
    )
    parser.add_argument(
        "--company", type=str,
        help="Single company name to research",
    )
    parser.add_argument(
        "--companies", type=str,
        help="Comma-separated list of company names",
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help=f"Path to companies file (default: {config.COMPANIES_FILE})",
    )
    parser.add_argument(
        "--backend", type=str, choices=["serper", "playwright", "auto"],
        help="Override SEARCH_BACKEND env var for this run",
    )
    parser.add_argument(
        "--max-concurrent", type=int, default=None,
        help=f"Max companies in parallel (default: {config.MAX_CONCURRENT_COMPANIES})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help=f"Output directory (default: {config.OUTPUT_DIR})",
    )
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()
    setup_logging(config.LOG_LEVEL)

    console.rule("[bold blue]🎯 ICP Search Engine[/bold blue]")

    # Override backend if specified via CLI
    if args.backend:
        import os
        os.environ["SEARCH_BACKEND"] = args.backend
        # Reload config values
        import importlib
        import config as cfg
        importlib.reload(cfg)

    # Validate config
    try:
        config.validate()
    except EnvironmentError as e:
        console.print(f"[red]❌ Configuration error:[/red]\n{e}")
        console.print("\n[yellow]Tip:[/yellow] Copy .env.example to .env and fill in your API keys.")
        sys.exit(1)

    # Determine company list
    if args.company:
        companies = [args.company.strip()]
    elif args.companies:
        companies = [c.strip() for c in args.companies.split(",") if c.strip()]
    else:
        file_path = args.file or config.COMPANIES_FILE
        companies = load_companies(file_path)

    # Drop duplicates while preserving order
    companies = deduplicate_companies(companies)

    output_dir = args.output_dir or config.OUTPUT_DIR
    max_concurrent = args.max_concurrent or config.MAX_CONCURRENT_COMPANIES

    console.print(f"[bold]Companies to process:[/bold] {len(companies)}")
    console.print(f"[bold]Search backend:[/bold] [cyan]{config.SEARCH_BACKEND}[/cyan]")
    console.print(f"[bold]Max concurrent:[/bold] {max_concurrent}")
    console.print(f"[bold]Output dir:[/bold] {output_dir}\n")

    # Run pipeline
    writer = ResultsWriter(Path(output_dir))
    results = await run_all(companies, writer, max_concurrent)

    # Print summary table
    console.print()
    print_summary(results)

    # Final file paths
    console.print()
    console.rule("[bold green]✅ Complete[/bold green]")
    console.print(f"📄 [bold]Detailed log:[/bold] [cyan]{writer.detail_path}[/cyan]")
    console.print(f"📊 [bold]Summary:[/bold]      [cyan]{writer.summary_path}[/cyan]")


if __name__ == "__main__":
    asyncio.run(main())
