"""
browser_utils.py
----------------
Shared Playwright browser management and anti-bot evasion utilities.

This module provides:
  - A singleton async Playwright context that is reused across all
    tool invocations to avoid cold-start overhead.
  - `new_stealth_page()`: Creates a new browser page pre-loaded with
    realistic User-Agent headers, viewport randomisation, and JavaScript
    injections that mask Playwright's automation fingerprints.
  - `jittered_sleep()`: Applies a randomised delay between requests to
    mimic human browsing cadence and avoid rate-limit triggers.
  - `safe_goto()`: Navigation wrapper with retry logic and structured
    error reporting.
"""
from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
#  Realistic User-Agent pool (desktop Chrome on Windows / macOS / Linux)
# ---------------------------------------------------------------------------
_USER_AGENTS: list[str] = [
    # Chrome 124 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    # Chrome 124 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    # Chrome 123 — Linux
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.6312.122 Safari/537.36"
    ),
    # Edge 124 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    # Firefox 125 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]

# JavaScript injected into every page to scrub Playwright automation flags
_STEALTH_JS: str = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Override plugins to non-empty
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// Override languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// Spoof Chrome runtime
window.chrome = { runtime: {} };

// Override permission query to always return 'granted'
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters);
"""

# ---------------------------------------------------------------------------
#  Singleton browser state
# ---------------------------------------------------------------------------
_playwright_instance: Playwright | None = None
_browser_instance: Browser | None = None


async def get_browser() -> Browser:
    """
    Return (or lazily initialise) the shared Playwright Browser instance.
    Chromium is used because it supports the widest range of stealth tricks.
    """
    global _playwright_instance, _browser_instance
    settings = get_settings()

    if _browser_instance is None or not _browser_instance.is_connected():
        logger.info("browser.launching", headless=settings.browser_headless)
        _playwright_instance = await async_playwright().start()
        _browser_instance = await _playwright_instance.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--disable-http2",
            ],
        )
    return _browser_instance


async def close_browser() -> None:
    """Gracefully shut down the browser and Playwright runtime."""
    global _playwright_instance, _browser_instance
    if _browser_instance:
        await _browser_instance.close()
        _browser_instance = None
    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None
    logger.info("browser.closed")


async def new_stealth_page() -> tuple[BrowserContext, Page]:
    """
    Create a new browser context + page with full anti-bot evasion:
      - Randomised User-Agent & Accept-Language headers
      - Randomised viewport (1280–1920 × 800–1080)
      - Stealth JS injection via init_script
      - Extra HTTP headers to mimic an organic browser session

    Returns
    -------
    (context, page) — The caller is responsible for closing the context
    after use to release memory.
    """
    browser = await get_browser()
    settings = get_settings()

    user_agent = random.choice(_USER_AGENTS)
    viewport_width = random.randint(1280, 1920)
    viewport_height = random.randint(800, 1080)

    context: BrowserContext = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": viewport_width, "height": viewport_height},
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "DNT": "1",
        },
    )
    await context.add_init_script(_STEALTH_JS)

    page: Page = await context.new_page()
    page.set_default_timeout(settings.browser_timeout_ms)

    logger.debug(
        "browser.page_created",
        user_agent=user_agent,
        viewport=f"{viewport_width}x{viewport_height}",
    )
    return context, page


async def jittered_sleep(base: float | None = None, extra_max: float | None = None) -> None:
    """
    Sleep for `base + uniform(0, extra_max)` seconds.
    Falls back to values from AppSettings when parameters are omitted.
    """
    settings = get_settings()
    b = base if base is not None else settings.base_request_delay_seconds
    m = extra_max if extra_max is not None else settings.max_jitter_seconds
    delay = b + random.uniform(0, m)
    logger.debug("request.delay", sleep_seconds=round(delay, 3))
    await asyncio.sleep(delay)


async def safe_goto(
    page: Page,
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    retries: int = 3,
    timeout_ms: int | None = None,
) -> bool:
    """
    Navigate to `url` with automatic retry on transient network failures.

    Parameters
    ----------
    page       : Active Playwright Page object.
    url        : Fully-qualified URL to navigate to.
    wait_until : Playwright wait strategy ('load', 'domcontentloaded',
                 'networkidle').
    retries    : Number of attempts before giving up.
    timeout_ms : Optional custom timeout in milliseconds.

    Returns
    -------
    True on success, False if all attempts failed.
    """
    for attempt in range(1, retries + 1):
        try:
            kwargs = {"wait_until": wait_until}
            if timeout_ms is not None:
                kwargs["timeout"] = timeout_ms
            await page.goto(url, **kwargs)
            logger.debug("browser.goto_ok", url=url, attempt=attempt)
            return True
        except Exception as exc:
            logger.warning(
                "browser.goto_failed",
                url=url,
                attempt=attempt,
                error=str(exc)[:200],
            )
            if attempt < retries:
                await jittered_sleep(base=2.0, extra_max=3.0)
    return False


@asynccontextmanager
async def stealth_page_ctx() -> AsyncGenerator[Page, None]:
    """
    Async context manager that yields a stealth page and guarantees the
    underlying BrowserContext is closed even if an exception is raised.

    Usage
    -----
    async with stealth_page_ctx() as page:
        await page.goto("https://example.com")
    """
    context, page = await new_stealth_page()
    try:
        yield page
    finally:
        await context.close()
