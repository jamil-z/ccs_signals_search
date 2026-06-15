"""
signals_mcp_server.py
---------------------
Asynchronous FastMCP server exposing four browser-automation tools that
harvest real-world buying signals for the Lead Enrichment Engine.

Tools exposed
-------------
  fetch_funding_signal       — Funding rounds & press events (SaaS ICP)
  fetch_operational_expansion — Store-locator / XHR geospatial data (Restaurant ICP)
  fetch_ats_telemetry        — ATS detection + job listing extraction
  fetch_technographic_profile — HR tech-stack keyword extraction from JDs

Run standalone (for local testing):
    python signals_mcp_server.py

The LangGraph orchestrator imports and calls these tools directly via the
MCP client interface rather than spawning a subprocess.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from typing import Any

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

from browser_utils import jittered_sleep, safe_goto, stealth_page_ctx
from config import get_settings
from schemas import ATSPlatform

logger = structlog.get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
#  FastMCP application instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="signals-scraper",
    version="1.0.0",
    description="Real-world buying-signal extraction tools for B2B lead enrichment.",
)

# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

_FUNDING_KEYWORDS: list[str] = [
    "Series A", "Series B", "Series C", "Series D",
    "Seed round", "raised", "funding", "investment",
    "venture capital", "million", "billion", "valuation",
    "acquisition", "acquired", "merger",
]

_HR_TECH_KEYWORDS: list[str] = [
    r"\bWorkday\b", r"\bBambooHR\b", r"\bRemote\.com\b", r"\bRippling\b",
    r"\bGreenhouse\b", r"\bLever\b", r"\bAshby\b", r"\bHRIS\b",
    r"\bADP\b", r"\bPaylocity\b", r"\bUKG\b", r"\bSAP\s*SuccessFactors\b",
    r"\bServiceNow\b", r"\bCeridian\b", r"\bNamely\b", r"\bHiBob\b",
    r"\bLattice\b", r"\bCulture\s*Amp\b", r"\bMercer\b",
]

_STORE_API_PATTERNS: list[str] = [
    r"list\.php\?json=true",
    r"/api/v\d+/stores",
    r"/api/v\d+/locations",
    r"store[_-]locator",
    r"findastoreSearch",
    r"/wp-json/.*locations",
    r"\.json\?lat=",
    r"mapbox.*geojson",
    r"google.*maps.*place",
]

_WORKDAY_JOB_PATTERN = re.compile(
    r"myworkdayjobs\.com|workday\.com/([^/]+)/([^/]+)/jobs"
)


def _extract_funding_snippets(text: str) -> str:
    """Return sentences from raw text that contain funding keywords."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits = [
        s.strip()
        for s in sentences
        if any(kw.lower() in s.lower() for kw in _FUNDING_KEYWORDS)
    ]
    return " | ".join(hits[:10]) if hits else ""


def _extract_tech_stack(text: str) -> list[str]:
    """Return deduplicated HR tech tool names found in raw job-description text."""
    found: set[str] = set()
    for pattern in _HR_TECH_KEYWORDS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found.add(match.group(0).strip())
    return sorted(found)


def _detect_ats_from_source(html: str) -> ATSPlatform:
    """Identify the ATS platform from careers page HTML source."""
    lower = html.lower()
    if "ashbyhq.com" in lower or "ashby" in lower:
        return ATSPlatform.ASHBY
    if "greenhouse.io" in lower or "boards.greenhouse" in lower:
        return ATSPlatform.GREENHOUSE
    if "lever.co" in lower:
        return ATSPlatform.LEVER
    if "myworkdayjobs.com" in lower or "workday.com" in lower:
        return ATSPlatform.WORKDAY
    return ATSPlatform.UNKNOWN


async def _fetch_workday_jobs(tenant: str, site: str) -> list[dict[str, Any]]:
    """
    Paginate through Workday's internal cxs jobs endpoint.
    Applies 20-item offsets until an empty page is returned.
    """
    base_url = f"https://{tenant}.wd1.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    all_jobs: list[dict[str, Any]] = []
    offset = 0
    limit = 20

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; LeadEngine/1.0)",
            "Accept": "application/json",
            "X-Calypso-CSRF-Token": "dummy",  # Required by Workday's CSRF check
        },
        timeout=15,
        follow_redirects=True,
    ) as client:
        while True:
            payload = {
                "limit": limit,
                "offset": offset,
                "searchText": "",
                "locations": [],
            }
            try:
                resp = await client.post(base_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                jobs = data.get("jobPostings", [])
                if not jobs:
                    break
                all_jobs.extend(jobs)
                offset += limit
                await jittered_sleep(base=1.0, extra_max=1.5)
            except Exception as exc:
                logger.warning("workday.pagination_error", offset=offset, error=str(exc)[:200])
                break

    return all_jobs


# ===========================================================================
#  TOOL 1 — Funding Signal
# ===========================================================================


@mcp.tool()
async def fetch_funding_signal(company_name: str, domain: str) -> str:
    """
    Scrape public press-release indices and search engines for funding
    events linked to the given company.

    Targets Google News search using structured intext: operators to
    surface Series-A/B/C rounds and M&A announcements. Public stock
    tickers are explicitly excluded to keep results VC-event-focused.

    Parameters
    ----------
    company_name : Human-readable company name (e.g. 'Rippling').
    domain       : Apex domain (e.g. 'rippling.com').

    Returns
    -------
    JSON string: {"snippets": "<extracted text>", "error": null | "<msg>"}
    """
    query = (
        f'"{company_name}" '
        f'(intext:"Series A" OR intext:"Series B" OR intext:"Series C" '
        f'OR intext:"funding" OR intext:"raised" OR intext:"acquisition") '
        f'-NYSE -NASDAQ -NYSE:MKT site:techcrunch.com OR site:crunchbase.com '
        f'OR site:businesswire.com OR site:prnewswire.com'
    )
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    try:
        async with stealth_page_ctx() as page:
            await jittered_sleep()
            success = await safe_goto(page, search_url, wait_until="domcontentloaded")
            if not success:
                return json.dumps({"snippets": "", "error": "Navigation failed for funding search."})

            await asyncio.sleep(2)
            raw_text = await page.evaluate("() => document.body.innerText")
            snippets = _extract_funding_snippets(raw_text)
            logger.info("funding_signal.ok", company=company_name, hits=len(snippets.split("|")))
            return json.dumps({"snippets": snippets, "error": None})

    except Exception as exc:
        err = f"fetch_funding_signal error: {str(exc)[:300]}"
        logger.error("funding_signal.error", company=company_name, error=err)
        return json.dumps({"snippets": "", "error": err})


# ===========================================================================
#  TOOL 2 — Operational Expansion
# ===========================================================================


@mcp.tool()
async def fetch_operational_expansion(company_name: str, domain: str) -> str:
    """
    Detect physical-location expansion by navigating to the company's
    store-locator or locations sub-page and intercepting hidden XHR calls
    that return geospatial store data.

    Strategy
    --------
    1. Register a route listener on the Playwright page to capture all
       network requests matching known store-API URL patterns.
    2. Navigate candidate paths: /store-locator, /locations, /stores, /find-a-store.
    3. If an XHR geospatial response is captured, parse the JSON and extract
       the store count and representative location names.
    4. Fall back to DOM text extraction if no API call is captured.

    Returns
    -------
    JSON string: {"store_count": int, "sample_locations": [...], "raw": "...", "error": null | "<msg>"}
    """
    candidate_paths = [
        "/store-locator", "/locations", "/stores",
        "/find-a-store", "/restaurants", "/find-a-restaurant",
        "/franchise-locations", "/our-locations",
    ]
    captured_xhr: list[str] = []

    def _matches_store_api(url: str) -> bool:
        return any(re.search(p, url, re.IGNORECASE) for p in _STORE_API_PATTERNS)

    try:
        async with stealth_page_ctx() as page:

            async def _intercept_response(response: Any) -> None:
                """Capture XHR responses that look like store APIs."""
                try:
                    if _matches_store_api(response.url) and response.status == 200:
                        body = await response.text()
                        captured_xhr.append(body[:8000])
                        logger.debug("expansion.xhr_captured", url=response.url[:120])
                except Exception:
                    pass

            page.on("response", _intercept_response)

            for path in candidate_paths:
                url = f"https://{domain}{path}"
                await jittered_sleep(base=1.0, extra_max=1.5)
                success = await safe_goto(page, url, wait_until="networkidle")
                if success:
                    # Allow lazy-loaded XHR calls to fire
                    await asyncio.sleep(3)
                    break

            # --- Parse captured XHR data ---
            store_count = 0
            sample_locations: list[str] = []
            raw_summary = ""

            if captured_xhr:
                for xhr_body in captured_xhr:
                    try:
                        data = json.loads(xhr_body)
                        # Handle both list and dict-wrapped list responses
                        if isinstance(data, list):
                            items = data
                        elif isinstance(data, dict):
                            items = (
                                data.get("stores")
                                or data.get("locations")
                                or data.get("results")
                                or data.get("features")  # GeoJSON
                                or []
                            )
                        else:
                            items = []

                        store_count += len(items)
                        for item in items[:5]:
                            if isinstance(item, dict):
                                city = (
                                    item.get("city")
                                    or item.get("name")
                                    or item.get("title")
                                    or ""
                                )
                                if city:
                                    sample_locations.append(str(city))
                    except json.JSONDecodeError:
                        raw_summary += xhr_body[:500]

            else:
                # Fall back: count DOM text occurrences of "location" / "store"
                dom_text = await page.evaluate("() => document.body.innerText")
                raw_summary = dom_text[:2000]
                store_matches = re.findall(
                    r"\b(?:location|store|restaurant|branch)\b",
                    dom_text,
                    re.IGNORECASE,
                )
                store_count = len(store_matches)

            logger.info(
                "expansion.ok",
                company=company_name,
                store_count=store_count,
                xhr_hits=len(captured_xhr),
            )
            return json.dumps({
                "store_count": store_count,
                "sample_locations": sample_locations[:10],
                "raw": raw_summary[:1500],
                "error": None,
            })

    except Exception as exc:
        err = f"fetch_operational_expansion error: {str(exc)[:300]}"
        logger.error("expansion.error", company=company_name, error=err)
        return json.dumps({"store_count": 0, "sample_locations": [], "raw": "", "error": err})


# ===========================================================================
#  TOOL 3 — ATS Telemetry
# ===========================================================================


@mcp.tool()
async def fetch_ats_telemetry(domain: str) -> str:
    """
    Identify the Applicant Tracking System used by the company and extract
    all open job postings.

    Detection flow
    --------------
    1. Load `https://{domain}/careers` (or /jobs) and analyse the HTML
       source for iframe src attributes or window.location redirects that
       expose a known ATS hostname.
    2. Branch on the detected ATS:
       - Ashby   → POST to api.ashbyhq.com/jobPosting.list
       - Greenhouse / Lever → Parse the embedded board widget DOM
       - Workday → Intercept XHR streams with paginated POST requests
       - Unknown → Best-effort DOM text extraction

    Returns
    -------
    JSON string: {
        "ats_platform": str,
        "job_count": int,
        "job_titles": [str, ...],
        "raw": str,
        "error": null | str
    }
    """
    careers_paths = ["/careers", "/jobs", "/join-us", "/work-with-us", "/about/careers"]

    try:
        async with stealth_page_ctx() as page:
            html_source = ""
            for path in careers_paths:
                url = f"https://{domain}{path}"
                await jittered_sleep()
                success = await safe_goto(page, url, wait_until="domcontentloaded")
                if success:
                    await asyncio.sleep(2)
                    html_source = await page.content()
                    if len(html_source) > 500:
                        break

            ats = _detect_ats_from_source(html_source)
            logger.info("ats.detected", domain=domain, ats=ats.value)

            # ---- Ashby ------------------------------------------------
            if ats == ATSPlatform.ASHBY:
                company_slug = domain.split(".")[0]
                ashby_url = "https://api.ashbyhq.com/jobPosting.list"
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        ashby_url,
                        json={"organizationHostedJobsPageName": company_slug},
                        headers={"Content-Type": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()

                jobs = data.get("results", [])
                titles = [j.get("title", "") for j in jobs if j.get("title")]
                return json.dumps({
                    "ats_platform": ats.value,
                    "job_count": len(jobs),
                    "job_titles": titles[:30],
                    "raw": json.dumps(jobs[:5]),
                    "error": None,
                })

            # ---- Workday -----------------------------------------------
            if ats == ATSPlatform.WORKDAY:
                # Extract tenant & site from page source or current URL
                wd_match = _WORKDAY_JOB_PATTERN.search(html_source + page.url)
                tenant = wd_match.group(1) if wd_match else domain.split(".")[0]
                site = wd_match.group(2) if wd_match else "External"
                jobs = await _fetch_workday_jobs(tenant, site)
                titles = [j.get("title", j.get("externalJobPostingName", "")) for j in jobs]
                return json.dumps({
                    "ats_platform": ats.value,
                    "job_count": len(jobs),
                    "job_titles": titles[:30],
                    "raw": "",
                    "error": None,
                })

            # ---- Greenhouse / Lever / Unknown ---------------------------
            # Parse visible job titles from DOM
            await page.wait_for_timeout(2000)
            dom_text = await page.evaluate("() => document.body.innerText")
            # Heuristic: lines that look like job titles (Title Case, < 80 chars)
            title_candidates = [
                line.strip()
                for line in dom_text.splitlines()
                if 10 < len(line.strip()) < 80
                and line.strip()[0].isupper()
                and not line.strip().startswith("©")
            ]
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique_titles = []
            for t in title_candidates:
                if t not in seen:
                    seen.add(t)
                    unique_titles.append(t)

            return json.dumps({
                "ats_platform": ats.value,
                "job_count": len(unique_titles),
                "job_titles": unique_titles[:30],
                "raw": dom_text[:1500],
                "error": None,
            })

    except Exception as exc:
        err = f"fetch_ats_telemetry error: {str(exc)[:300]}"
        logger.error("ats.error", domain=domain, error=err)
        return json.dumps({
            "ats_platform": ATSPlatform.UNKNOWN.value,
            "job_count": 0,
            "job_titles": [],
            "raw": "",
            "error": err,
        })


# ===========================================================================
#  TOOL 4 — Technographic Profile
# ===========================================================================


@mcp.tool()
async def fetch_technographic_profile(domain: str) -> str:
    """
    Extract HR/People-ops technology requirements from active job postings
    using regex word-boundary matching.

    Navigates to the careers page, expands job descriptions by clicking
    available listing links, and scans accumulated text for explicitly
    named software tools (Workday, BambooHR, Rippling, etc.).

    Returns
    -------
    JSON string: {
        "detected_tools": [str, ...],
        "raw_excerpt": str,
        "error": null | str
    }
    """
    careers_url = f"https://{domain}/careers"

    try:
        async with stealth_page_ctx() as page:
            await jittered_sleep()
            success = await safe_goto(page, careers_url, wait_until="domcontentloaded")

            if not success:
                # Try /jobs fallback
                await safe_goto(page, f"https://{domain}/jobs", wait_until="domcontentloaded")

            await asyncio.sleep(2)
            full_text = await page.evaluate("() => document.body.innerText")

            # Try to expand up to 3 individual job descriptions for richer signal
            job_links = await page.query_selector_all("a[href*='/job'], a[href*='/careers/'], a[href*='/jobs/']")
            for link in job_links[:3]:
                try:
                    href = await link.get_attribute("href")
                    if href:
                        jd_url = href if href.startswith("http") else f"https://{domain}{href}"
                        ctx2, jd_page = await page.context.browser.new_context().__aenter__()  # type: ignore[attr-defined]
                        # Simpler: open in same page sequentially
                        await jittered_sleep(base=1.0, extra_max=1.0)
                        await safe_goto(page, jd_url, wait_until="domcontentloaded")
                        await asyncio.sleep(1)
                        jd_text = await page.evaluate("() => document.body.innerText")
                        full_text += "\n" + jd_text
                except Exception:
                    pass

            detected = _extract_tech_stack(full_text)
            logger.info("technographic.ok", domain=domain, tools=detected)
            return json.dumps({
                "detected_tools": detected,
                "raw_excerpt": full_text[:2000],
                "error": None,
            })

    except Exception as exc:
        err = f"fetch_technographic_profile error: {str(exc)[:300]}"
        logger.error("technographic.error", domain=domain, error=err)
        return json.dumps({"detected_tools": [], "raw_excerpt": "", "error": err})


# ===========================================================================
#  Standalone entry point
# ===========================================================================

if __name__ == "__main__":
    import sys
    # Run as an MCP server over stdio when invoked directly
    mcp.run(transport="stdio")
