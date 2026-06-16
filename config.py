"""
config.py — Central configuration loader for the ICP Search Engine.
All settings come from environment variables (loaded from .env).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Azure OpenAI ──────────────────────────────────────────────────────────────
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")

# ── Search Backend ────────────────────────────────────────────────────────────
# "serper" | "playwright" | "auto"
SEARCH_BACKEND: str = os.getenv("SEARCH_BACKEND", "serper")
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
SERPER_BASE_URL: str = "https://google.serper.dev/search"

# ── Playwright ────────────────────────────────────────────────────────────────
BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT_MS: int = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))

# ── Concurrency & Rate Limiting ───────────────────────────────────────────────
MAX_CONCURRENT_COMPANIES: int = int(os.getenv("MAX_CONCURRENT_COMPANIES", "2"))
BASE_REQUEST_DELAY: float = float(os.getenv("BASE_REQUEST_DELAY_SECONDS", "1.5"))
MAX_JITTER: float = float(os.getenv("MAX_JITTER_SECONDS", "1.5"))

# ── Paths ─────────────────────────────────────────────────────────────────────
COMPANIES_FILE: Path = Path(os.getenv("COMPANIES_FILE", "./companies.txt"))
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "./outputs"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── MCP Server Commands ───────────────────────────────────────────────────────
# These are the commands used to spin up the MCP servers
MCP_PLAYWRIGHT_CMD: list[str] = ["npx", "-y", "@playwright/mcp@latest"]
MCP_FILESYSTEM_CMD: list[str] = [
    "npx", "-y", "@modelcontextprotocol/server-filesystem",
    str(OUTPUT_DIR.resolve()),
]

# ── Validate critical settings ────────────────────────────────────────────────
def validate():
    """Raise on missing critical configuration."""
    errors = []
    if not AZURE_OPENAI_API_KEY:
        errors.append("AZURE_OPENAI_API_KEY is not set")
    if not AZURE_OPENAI_ENDPOINT:
        errors.append("AZURE_OPENAI_ENDPOINT is not set")
    if SEARCH_BACKEND in ("serper", "auto") and not SERPER_API_KEY:
        errors.append(
            "SERPER_API_KEY is not set (required when SEARCH_BACKEND=serper or auto). "
            "Get 2,500 free searches at https://serper.dev"
        )
    if errors:
        raise EnvironmentError("\n".join(f"  ✗ {e}" for e in errors))
