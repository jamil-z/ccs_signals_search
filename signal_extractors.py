"""
signal_extractors.py — 3 QSR buying signal extractors.

Performance architecture:
  1. httpx domain-reachability check FIRST (< 1s) — bail out immediately if domain is dead
  2. Playwright with `load` wait (NOT networkidle) — exits after HTML is parsed, ignores analytics
  3. Only 4 highest-probability paths tried, not 8
  4. 1 retry only for timeouts; 0 retries for connection refused (dead domain)
  5. Per-page timeout: 12s (not 30s)
"""
from __future__ import annotations
import asyncio, hashlib, json, re, urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
import httpx, structlog
from browser_utils import jittered_sleep, safe_goto, stealth_page_ctx
from cache_manager import CacheManager
from config import get_settings
from schemas import (
    ATSHiringResult, ATSPlatform, HiringSignalStrength,
    JobPosting, OSINTArticle, OSINTResult, StoreExpansionResult,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Patterns ──────────────────────────────────────────────────────────────
_STORE_API_PATTERNS = [
    r"list\.php\?json=true", r"/api/v\d+/stores", r"/api/v\d+/locations",
    r"/api/v\d+/restaurants", r"store[_-]locator", r"findastoreSearch",
    r"/wp-json/.*locations", r"\.json\?lat=", r"storeSearch",
    r"restaurant[_-]?list", r"boundary=", r"mapbox.*geojson",
    r"/graphql.*location", r"getStores", r"getNearby",
]

_QSR_ATS_SIGNATURES = {
    ATSPlatform.PARADOX:    ["paradox.ai", "olivia.paradox.ai"],
    ATSPlatform.HARRI:      ["harri.com", "app.harri.com"],
    ATSPlatform.SNAGAJOB:   ["snagajob.com"],
    ATSPlatform.WORKDAY:    ["myworkdayjobs.com", "workday.com/"],
    ATSPlatform.GREENHOUSE: ["greenhouse.io", "boards.greenhouse"],
    ATSPlatform.LEVER:      ["lever.co"],
    ATSPlatform.ASHBY:      ["ashbyhq.com"],
}

_CRITICAL_ROLE_RE = re.compile(
    r"\b(shift supervisor|shift manager|general manager|district manager"
    r"|hr manager|store manager|area manager|franchise manager|crew leader"
    r"|director of operations|training manager|payroll|recruiter)\b", re.I)

_CONSOLIDATION_RE = re.compile(
    r"\b(acqui(?:res?|red|sition)|merger|opens? new|new location|expand"
    r"|franchise deal|multi.unit|portfolio|growth strategy)\b", re.I)

# Priority-ordered paths — most common first, stop on first hit
_STORE_PATHS = ["/locations", "/store-locator", "/restaurants", "/find-a-store"]
_CAREERS_PATHS = ["/careers", "/jobs", "/about/careers", "/join-us"]


# ── Helpers ───────────────────────────────────────────────────────────────

def _matches_store_api(url: str) -> bool:
    return any(re.search(p, url, re.I) for p in _STORE_API_PATTERNS)

def _detect_ats(html: str, redirect_url: str) -> ATSPlatform:
    combined = (html + " " + redirect_url).lower()
    for platform, sigs in _QSR_ATS_SIGNATURES.items():
        if any(s in combined for s in sigs):
            return platform
    return ATSPlatform.UNKNOWN

def _make_job_hash(title: str, store_ref: str) -> str:
    return hashlib.sha256(f"{title.lower().strip()}:{store_ref.lower().strip()}".encode()).hexdigest()[:16]

def _extract_store_ref(title: str) -> str:
    m = re.search(r"(store\s*#?\d+|location\s*#?\d+|#\d+)", title, re.I)
    return m.group(0) if m else ""

def _classify_hiring_strength(reqs: int, churn: int) -> HiringSignalStrength:
    if reqs >= 50 or churn >= 5: return HiringSignalStrength.HIGH
    if reqs >= 10 or churn >= 2: return HiringSignalStrength.MEDIUM
    return HiringSignalStrength.LOW

def _extract_publication(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m: return ""
    domain = m.group(1).lower()
    pub_map = {
        "qsrmagazine.com": "QSR Magazine", "franchisetimes.com": "Franchise Times",
        "nrn.com": "Nation\'s Restaurant News", "businesswire.com": "Business Wire",
        "prnewswire.com": "PR Newswire", "reuters.com": "Reuters",
        "wsj.com": "Wall Street Journal", "bloomberg.com": "Bloomberg",
        "restaurantbusinessonline.com": "Restaurant Business",
        "globenewswire.com": "Globe Newswire", "forbes.com": "Forbes",
    }
    for k, v in pub_map.items():
        if k in domain: return v
    return domain.replace("www.", "").split(".")[0].title()

def _build_dork_query(company_name: str) -> str:
    return (
        f'"{company_name}" '
        f'(acquires OR "new locations" OR "new restaurant" OR expansion OR '
        f'"opens new" OR franchise OR "multi-unit" OR merger) '
        f'-yelp -tripadvisor -doordash -ubereats -grubhub -opentable'
    )

async def _domain_is_reachable(domain: str) -> str | None:
    """
    Fast HEAD request to check if domain resolves and responds.
    Returns the final resolved domain host on success, None if dead.
    """
    for scheme in ("https", "http"):
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.head(
                    f"{scheme}://{domain}",
                    headers={"User-Agent": "Mozilla/5.0 (compatible; SignalBot/1.0)"},
                )
                if resp.status_code < 500:
                    final_host = resp.url.host
                    logger.info("domain.reachable", domain=domain, resolved=final_host, status=resp.status_code)
                    return final_host
        except Exception:
            pass
    logger.warning("domain.unreachable", domain=domain)
    return None

async def _fetch_workday_jobs(tenant: str, site: str) -> list[dict]:
    base = f"https://{tenant}.wd1.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    all_jobs: list[dict] = []
    offset = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0", "X-Calypso-CSRF-Token": "dummy"},
        timeout=12, follow_redirects=True
    ) as client:
        while True:
            try:
                resp = await client.post(base, json={"limit": 20, "offset": offset, "searchText": ""})
                resp.raise_for_status()
                jobs = resp.json().get("jobPostings", [])
                if not jobs: break
                all_jobs.extend(jobs)
                offset += 20
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.warning("workday.error", error=str(exc)[:100])
                break
    return all_jobs


# ══════════════════════════════════════════════════════════════════════════
#  SIGNAL 1 — Store Expansion (XHR interception)
# ══════════════════════════════════════════════════════════════════════════

async def extract_store_expansion(company_name: str, domain: str, cache: CacheManager) -> StoreExpansionResult:
    """Intercept hidden XHR calls on store-locator page to count/delta locations."""
    cached = cache.get_cached(company_name, "store_locator")
    if cached:
        r = StoreExpansionResult(**cached); r.from_cache = True; return r

    # ── 1. Domain reachability check (fast bail-out) ──────────────────────
    resolved_domain = await _domain_is_reachable(domain)
    if not resolved_domain:
        result_data = {
            "source_url": "", "store_count_today": 0, "store_count_delta": 0,
            "new_store_ids": [], "sample_locations": [], "expansion_detected": False,
            "from_cache": False, "error": f"Domain {domain} is unreachable",
        }
        cache.set_cached(company_name, "store_locator", result_data, 1)  # Cache for 1h only
        return StoreExpansionResult(**result_data)

    captured_xhr: list[str] = []
    source_url = ""

    try:
        async with stealth_page_ctx() as page:
            async def _capture(response: Any) -> None:
                try:
                    if _matches_store_api(response.url) and response.status == 200:
                        body = await response.text()
                        if len(body) > 100:
                            captured_xhr.append(body[:12000])
                            logger.info("store_xhr.captured", url=response.url[:100])
                except Exception: pass

            page.on("response", _capture)

            for path in _STORE_PATHS:
                url = f"https://{resolved_domain}{path}"
                logger.info("store_locator.trying", company=company_name, url=url)
                await jittered_sleep(base=0.5, extra_max=0.8)
                # Use "load" NOT "networkidle" — networkidle hangs on sites with analytics
                ok = await safe_goto(page, url, wait_until="load", retries=1, timeout_ms=10000)
                if ok:
                    source_url = url
                    await asyncio.sleep(2)  # Brief wait for XHR to fire
                    if captured_xhr:
                        logger.info("store_locator.xhr_found", url=url, count=len(captured_xhr))
                        break
                    logger.info("store_locator.no_xhr_yet", url=url)

            # ── Parse XHR payloads ────────────────────────────────────────
            store_count, store_ids, sample_locations = 0, [], []
            if captured_xhr:
                for body in captured_xhr:
                    try:
                        data = json.loads(body)
                        items = data if isinstance(data, list) else next(
                            (data[k] for k in ("stores","locations","results","features","restaurants","data")
                             if isinstance(data.get(k), list)), [])
                        store_count += len(items)
                        for item in items:
                            if not isinstance(item, dict): continue
                            sid = str(item.get("storeId") or item.get("store_id") or item.get("id") or item.get("locationId") or store_count)
                            store_ids.append(sid)
                            city = item.get("city") or item.get("name") or item.get("title") or ""
                            if city and len(sample_locations) < 10:
                                sample_locations.append(str(city))
                    except Exception: pass
            else:
                # Fallback: count keyword density in DOM
                if source_url:
                    try:
                        dom = await page.evaluate("() => document.body.innerText")
                        store_count = len(re.findall(r"\b(?:location|store|restaurant|branch)\b", dom, re.I))
                    except Exception:
                        store_count = 0
                else:
                    store_count = 0

            delta, new_ids = cache.compute_store_delta(resolved_domain, store_count, store_ids)
            result_data = {
                "source_url": source_url, "store_count_today": store_count,
                "store_count_delta": delta, "new_store_ids": new_ids[:20],
                "sample_locations": sample_locations, "expansion_detected": delta > 0,
                "from_cache": False, "error": "",
            }
            cache.set_cached(company_name, "store_locator", result_data, settings.cache_ttl_store_locator_hours)
            logger.info("store_expansion.ok", company=company_name, count=store_count, delta=delta, xhr=len(captured_xhr))
            return StoreExpansionResult(**result_data)

    except Exception as exc:
        err = f"extract_store_expansion: {str(exc)[:300]}"
        logger.error("store_expansion.error", company=company_name, error=err)
        return StoreExpansionResult(error=err)


# ══════════════════════════════════════════════════════════════════════════
#  SIGNAL 2 — ATS Hiring / Churn
# ══════════════════════════════════════════════════════════════════════════

async def extract_ats_hiring(company_name: str, domain: str, cache: CacheManager) -> ATSHiringResult:
    """Detect ATS platform via /careers redirect, extract jobs, compute churn."""
    cached = cache.get_cached(company_name, "ats_hiring")
    if cached:
        r = ATSHiringResult(**cached); r.from_cache = True; return r

    # ── 1. Domain reachability check ─────────────────────────────────────
    resolved_domain = await _domain_is_reachable(domain)
    if not resolved_domain:
        result_data = {
            "ats_platform": ATSPlatform.UNKNOWN.value, "ats_url": "", "open_requisitions": 0,
            "jobs": [], "churn_anomalies": 0, "critical_roles": [],
            "hiring_signal_strength": HiringSignalStrength.LOW.value,
            "from_cache": False, "error": f"Domain {domain} is unreachable",
        }
        cache.set_cached(company_name, "ats_hiring", result_data, 1)
        return ATSHiringResult(**result_data)

    try:
        async with stealth_page_ctx() as page:
            html_source, final_url = "", ""
            for path in _CAREERS_PATHS:
                url = f"https://{resolved_domain}{path}"
                logger.info("ats.trying", company=company_name, url=url)
                await jittered_sleep(base=0.5, extra_max=0.8)
                if await safe_goto(page, url, wait_until="domcontentloaded", retries=1, timeout_ms=10000):
                    await asyncio.sleep(2)
                    html_source = await page.content()
                    final_url = page.url
                    logger.info("ats.page_loaded", url=url, redirected_to=final_url[:80], html_len=len(html_source))
                    if len(html_source) > 500: break

            ats = _detect_ats(html_source, final_url)
            raw_titles: list[str] = []

            if ats == ATSPlatform.ASHBY:
                slug = domain.split(".")[0]
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post("https://api.ashbyhq.com/jobPosting.list",
                                            json={"organizationHostedJobsPageName": slug})
                    raw_titles = [j.get("title","") for j in resp.json().get("results",[]) if j.get("title")]

            elif ats == ATSPlatform.WORKDAY:
                wd = re.search(r"myworkdayjobs\.com/([^/\"]+)/([^/\"]+)", html_source + final_url)
                tenant = wd.group(1) if wd else resolved_domain.split(".")[0]
                site = wd.group(2) if wd else "External"
                jobs_data = await _fetch_workday_jobs(tenant, site)
                raw_titles = [j.get("title") or j.get("externalJobPostingName","") for j in jobs_data]

            else:
                # Generic DOM extraction
                if final_url:
                    try:
                        await page.wait_for_timeout(1500)
                        dom = await page.evaluate("() => document.body.innerText")
                        seen: set[str] = set()
                        for line in dom.splitlines():
                            l = line.strip()
                            if 8 < len(l) < 100 and l[0].isupper() and l not in seen:
                                seen.add(l); raw_titles.append(l)
                    except Exception:
                        pass
                raw_titles = raw_titles[:60]

            job_postings, lifecycle_input, critical_roles = [], [], []
            for title in raw_titles:
                if not title: continue
                store_ref = _extract_store_ref(title)
                job_hash = _make_job_hash(title, store_ref)
                job_postings.append(JobPosting(title=title, store_ref=store_ref, job_hash=job_hash))
                lifecycle_input.append({"title": title, "store_ref": store_ref, "job_hash": job_hash})
                if _CRITICAL_ROLE_RE.search(title): critical_roles.append(title)

            churn = cache.update_job_lifecycle(resolved_domain, lifecycle_input)
            open_reqs = len(job_postings)
            strength = _classify_hiring_strength(open_reqs, churn)
            result_data = {
                "ats_platform": ats.value, "ats_url": final_url, "open_requisitions": open_reqs,
                "jobs": [j.model_dump() for j in job_postings[:30]], "churn_anomalies": churn,
                "critical_roles": list(set(critical_roles))[:10],
                "hiring_signal_strength": strength.value, "from_cache": False, "error": "",
            }
            cache.set_cached(company_name, "ats_hiring", result_data, settings.cache_ttl_ats_hours)
            logger.info("ats_hiring.ok", company=company_name, ats=ats.value, open=open_reqs, churn=churn, strength=strength.value)
            return ATSHiringResult(**result_data)

    except Exception as exc:
        err = f"extract_ats_hiring: {str(exc)[:300]}"
        logger.error("ats_hiring.error", company=company_name, error=err)
        return ATSHiringResult(error=err)


# ══════════════════════════════════════════════════════════════════════════
#  SIGNAL 3 — OSINT / Google News (Serper preferred, Playwright fallback)
# ══════════════════════════════════════════════════════════════════════════

async def _osint_via_rss(company_name: str, query: str) -> list[OSINTArticle]:
    """
    Fetch news articles using Google News RSS.
    Does not require Playwright/browser, is extremely fast (<1s), and avoids robot/CAPTCHA blocks.
    """
    import urllib.parse as _up
    encoded = _up.quote_plus(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    articles: list[OSINTArticle] = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    try:
        logger.info("osint.rss_fetch", company=company_name, url=rss_url[:80])
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(rss_url, headers=headers)
            resp.raise_for_status()
            
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        for item in items[:10]:
            title_el = item.find("title")
            link_el = item.find("link")
            pub_date_el = item.find("pubDate")
            source_el = item.find("source")
            
            headline = title_el.text if title_el is not None else ""
            url = link_el.text if link_el is not None else ""
            pub_date = pub_date_el.text if pub_date_el is not None else ""
            publication = source_el.text if source_el is not None else ""
            
            if headline:
                articles.append(OSINTArticle(
                    headline=headline,
                    publication=publication or _extract_publication(url),
                    pub_date=pub_date,
                    url=url
                ))
        logger.info("osint.rss_done", company=company_name, articles_found=len(articles))
    except Exception as exc:
        logger.warning("osint.rss_failed", company=company_name, error=str(exc)[:150])
        
    return articles


async def _osint_via_playwright(company_name: str, query: str) -> list[OSINTArticle]:
    """Fallback: Playwright on Google News — no API key required."""
    import urllib.parse as _up
    encoded = _up.quote_plus(query)
    search_url = f"https://news.google.com/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    articles: list[OSINTArticle] = []
    try:
        async with stealth_page_ctx() as page:
            await jittered_sleep(base=1.0, extra_max=1.5)
            logger.info("osint.playwright_navigate", company=company_name, url=search_url[:80])
            ok = await safe_goto(page, search_url, wait_until="domcontentloaded", retries=1, timeout_ms=10000)
            if not ok: return articles
            await asyncio.sleep(3)
            cards = await page.evaluate("""() => {
                const results = [];
                document.querySelectorAll("article").forEach(a => {
                    const title = a.querySelector("h3,h4");
                    const source = a.querySelector("time ~ span, [data-n-tid]");
                    const time_el = a.querySelector("time");
                    const link_el = a.querySelector("a[href]");
                    if (title) results.push({
                        headline: title.innerText.trim(),
                        publication: source ? source.innerText.trim() : "",
                        pub_date: time_el ? (time_el.getAttribute("datetime") || time_el.innerText.trim()) : "",
                        url: link_el ? link_el.href : ""
                    });
                });
                return results.slice(0, 10);
            }""")
            for card in cards:
                h = card.get("headline","").strip()
                if h and len(h) > 10:
                    articles.append(OSINTArticle(
                        headline=h, publication=card.get("publication","").strip(),
                        pub_date=card.get("pub_date","").strip(), url=card.get("url",""),
                    ))
        logger.info("osint.playwright_done", company=company_name, articles_found=len(articles))
    except Exception as exc:
        logger.warning("osint.playwright_error", company=company_name, error=str(exc)[:150])
    return articles


async def extract_osint_news(company_name: str, cache: CacheManager) -> OSINTResult:
    """
    Signal 3: OSINT news. Order: 1) Cache  2) Serper API  3) RSS Google News  4) Playwright fallback.
    Never blocks the pipeline — returns empty result gracefully if all fail.
    """
    cached = cache.get_cached(company_name, "osint_news")
    if cached:
        r = OSINTResult(**cached); r.from_cache = True; return r

    query = _build_dork_query(company_name)
    articles: list[OSINTArticle] = []
    source_used = "none"

    # Strategy 1: Serper API (if key configured)
    serper_key = settings.serper_api_key
    if serper_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": 10, "gl": "us", "hl": "en"},
                )
                resp.raise_for_status()
                data = resp.json()
            for item in data.get("organic", []) + data.get("news", []):
                h, u = item.get("title",""), item.get("link","")
                if h:
                    articles.append(OSINTArticle(headline=h, publication=_extract_publication(u),
                                                  pub_date=item.get("date",""), url=u))
            source_used = "serper"
            logger.info("osint.serper_ok", company=company_name, articles=len(articles))
        except Exception as exc:
            logger.warning("osint.serper_failed", company=company_name, error=str(exc)[:100])

    # Strategy 2: Google News RSS (Fast, free, robust fallback)
    if not articles:
        logger.info("osint.using_rss_fallback", company=company_name)
        articles = await _osint_via_rss(company_name, query)
        source_used = "rss"

    # Strategy 3: Playwright on Google News (Absolute last resort fallback)
    if not articles:
        logger.info("osint.using_playwright_fallback", company=company_name)
        articles = await _osint_via_playwright(company_name, query)
        source_used = "playwright"

    consolidation_detected = any(_CONSOLIDATION_RE.search(a.headline) for a in articles)
    top = articles[0] if articles else OSINTArticle()
    result_data = {
        "query_used": query, "articles_found": len(articles),
        "articles": [a.model_dump() for a in articles],
        "consolidation_detected": consolidation_detected,
        "top_headline": top.headline, "top_publication": top.publication,
        "top_date": top.pub_date, "from_cache": False, "error": "",
    }
    cache.set_cached(company_name, "osint_news", result_data, settings.cache_ttl_osint_hours)
    logger.info("osint.complete", company=company_name, source=source_used,
                articles=len(articles), consolidation=consolidation_detected)
    return OSINTResult(**result_data)
