"""
search_tools.py — Pluggable search abstraction layer.

Architecture:
  buscar(query) → SearchResult

The rest of the codebase ONLY calls `buscar()`. The backend (Serper vs Playwright)
is chosen at startup via config.SEARCH_BACKEND and is completely transparent to callers.

Switching backends = change one env var, zero code changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from config import (
    BASE_REQUEST_DELAY,
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT_MS,
    MAX_JITTER,
    SEARCH_BACKEND,
    SERPER_API_KEY,
    SERPER_BASE_URL,
)

logger = logging.getLogger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """Normalised result from any backend."""
    query: str
    backend: Literal["serper", "playwright", "mock"]
    organic: list[dict] = field(default_factory=list)  # [{title, link, snippet}]
    raw_html: str = ""       # page HTML (playwright) or "" (serper)
    raw_json: dict = field(default_factory=dict)  # full serper response or {}
    success: bool = True
    error: str = ""

    @property
    def top_links(self) -> list[str]:
        return [r["link"] for r in self.organic if "link" in r]

    @property
    def first_link(self) -> str:
        return self.top_links[0] if self.top_links else ""

    @property
    def text_summary(self) -> str:
        """Plain-text summary: title + snippet for each result."""
        lines = []
        for r in self.organic[:8]:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            link = r.get("link", "")
            lines.append(f"• [{title}]({link})\n  {snippet}")
        return "\n".join(lines)

    @property
    def html_snippet(self) -> str:
        """First ~600 chars of raw_html (for CSV logging)."""
        return self.raw_html[:600] if self.raw_html else ""


# ── Rate limiter ──────────────────────────────────────────────────────────────

async def _rate_limit():
    delay = BASE_REQUEST_DELAY + random.uniform(0, MAX_JITTER)
    await asyncio.sleep(delay)


# ── Backend: Serper.dev ───────────────────────────────────────────────────────

async def _search_serper(query: str, num_results: int = 10) -> SearchResult:
    """
    Call the Serper.dev Google Search API.
    Docs: https://serper.dev/docs
    Free tier: 2,500 searches/month.
    """
    if not SERPER_API_KEY:
        return SearchResult(
            query=query,
            backend="serper",
            success=False,
            error="SERPER_API_KEY not set. Add it to your .env file.",
        )

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": num_results, "gl": "us", "hl": "en"}

    try:
        await _rate_limit()
        # Explicit 15 s connect + read timeout — prevents indefinite hangs on slow API responses.
        _timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=_timeout) as client:
            resp = await client.post(SERPER_BASE_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        organic = [
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in data.get("organic", [])
        ]

        logger.debug(f"[serper] '{query}' → {len(organic)} results")
        return SearchResult(
            query=query,
            backend="serper",
            organic=organic,
            raw_json=data,
            success=True,
        )

    except httpx.TimeoutException as exc:
        logger.error(f"[serper] Timeout for '{query}': {exc}")
        return SearchResult(query=query, backend="serper", success=False, error=f"Timeout: {exc}")
    except Exception as exc:
        logger.error(f"[serper] Error for '{query}': {exc}")
        return SearchResult(query=query, backend="serper", success=False, error=str(exc))


# ── Backend: Playwright (via MCP or direct) ───────────────────────────────────

async def _search_playwright(query: str, num_results: int = 10) -> SearchResult:
    """
    Use Playwright to scrape Google Search results.

    Note: Google actively blocks headless browsers. This backend is provided
    as an alternative for cases where Serper is not available. Use Serper in
    production to avoid CAPTCHAs. This implementation uses playwright-python.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SearchResult(
            query=query,
            backend="playwright",
            success=False,
            error="playwright not installed. Run: pip install playwright && playwright install chromium",
        )

    encoded = httpx.URL(params={"q": query, "num": str(num_results)})
    url = f"https://www.google.com/search?{encoded.params}"

    try:
        await _rate_limit()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ],
            )
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)

            # Check for CAPTCHA
            content = await page.content()
            if "unusual traffic" in content.lower() or "captcha" in content.lower():
                await browser.close()
                return SearchResult(
                    query=query,
                    backend="playwright",
                    success=False,
                    error="Google CAPTCHA detected. Use Serper backend for production.",
                )

            # Extract organic results
            results = await page.evaluate("""() => {
                const items = [];
                document.querySelectorAll('#search .g').forEach(el => {
                    const titleEl = el.querySelector('h3');
                    const linkEl = el.querySelector('a');
                    const snippetEl = el.querySelector('.VwiC3b, .s3v9rd, span');
                    if (titleEl && linkEl) {
                        items.push({
                            title: titleEl.innerText,
                            link: linkEl.href,
                            snippet: snippetEl ? snippetEl.innerText : ''
                        });
                    }
                });
                return items;
            }""")

            raw_html = content[:3000]  # Save first 3KB
            await browser.close()

        logger.debug(f"[playwright] '{query}' → {len(results)} results")
        return SearchResult(
            query=query,
            backend="playwright",
            organic=results[:num_results],
            raw_html=raw_html,
            success=True,
        )

    except Exception as exc:
        logger.error(f"[playwright] Error for '{query}': {exc}")
        return SearchResult(query=query, backend="playwright", success=False, error=str(exc))


# ── Public interface ──────────────────────────────────────────────────────────

# Hard cap applied to every buscar() call — prevents the event loop from hanging
# if the underlying backend stalls (e.g., API unresponsive, Playwright hangs).
_BUSCAR_TIMEOUT_SECONDS = 30


async def _buscar_inner(
    query: str,
    num_results: int,
    backend: str,
) -> SearchResult:
    """Route to the correct backend without any timeout guard (timeout is applied by caller)."""
    if backend == "serper":
        return await _search_serper(query, num_results)
    elif backend == "playwright":
        return await _search_playwright(query, num_results)
    elif backend == "auto":
        result = await _search_serper(query, num_results)
        if not result.success:
            logger.warning(f"Serper failed ({result.error}), falling back to Playwright…")
            result = await _search_playwright(query, num_results)
        return result
    else:
        raise ValueError(f"Unknown SEARCH_BACKEND: '{backend}'. Use 'serper', 'playwright', or 'auto'.")


async def buscar(
    query: str,
    num_results: int = 10,
    force_backend: str | None = None,
) -> SearchResult:
    """
    Universal search function — the ONLY entry point for the rest of the codebase.

    Parameters
    ----------
    query        : The search query string (built by each pipeline phase).
    num_results  : How many results to request.
    force_backend: Override SEARCH_BACKEND for this specific call.
                   Options: "serper" | "playwright"

    Returns
    -------
    SearchResult with normalised organic results, raw HTML/JSON, and metadata.

    Backend selection (config.SEARCH_BACKEND):
    - "serper"    -> Always use Serper API (recommended for production).
    - "playwright"-> Always use headless browser (risk of CAPTCHAs).
    - "auto"      -> Try Serper first; fall back to Playwright on failure.

    Timeout
    -------
    Each call is guarded by a hard asyncio timeout (_BUSCAR_TIMEOUT_SECONDS).
    On timeout the function returns a failed SearchResult instead of hanging.
    """
    backend = force_backend or SEARCH_BACKEND
    try:
        return await asyncio.wait_for(
            _buscar_inner(query, num_results, backend),
            timeout=_BUSCAR_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"[buscar] Hard timeout ({_BUSCAR_TIMEOUT_SECONDS}s) exceeded for query: '{query}'"
        )
        return SearchResult(
            query=query,
            backend=backend,  # type: ignore[arg-type]
            success=False,
            error=f"Timeout after {_BUSCAR_TIMEOUT_SECONDS}s",
        )


async def buscar_url(url: str) -> SearchResult:
    """
    Fetch and return the HTML content of a specific URL using Playwright.
    Used for Phase 2.3 (customer volume page scraping) and similar tasks.
    Always uses Playwright regardless of SEARCH_BACKEND setting.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return SearchResult(
            query=url,
            backend="playwright",
            success=False,
            error="playwright not installed",
        )

    try:
        await _rate_limit()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=["--no-sandbox"],
            )
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            content = await page.content()
            text = await page.evaluate("() => document.body.innerText")
            await browser.close()

        return SearchResult(
            query=url,
            backend="playwright",
            organic=[{"title": url, "link": url, "snippet": text[:500]}],
            raw_html=content[:3000],
            success=True,
        )
    except Exception as exc:
        return SearchResult(query=url, backend="playwright", success=False, error=str(exc))
