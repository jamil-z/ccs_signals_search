# Lead Enrichment & Signal Extraction Engine

A production-grade, stateful multi-agent system for harvesting real-world
buying signals and synthesising hyper-personalised cold-outreach context.

---

## Architecture Overview

```
test_leads.json
      │
      ▼
   main.py  ──── asyncio.Semaphore (concurrency cap)
      │
      ▼
 LangGraph StateGraph  (graph_orchestrator.py)
  ┌───────────────────────────────────────┐
  │  routing_node                         │  ICP classification
  │       ↓                               │
  │  extraction_worker_node               │  asyncio.gather() over MCP tools
  │       ↓                               │
  │  llm_synthesis_node                   │  Azure OpenAI gpt-5.5
  └───────────────────────────────────────┘
      │
      ▼
 outputs/output_{domain}.json  (EnrichedPayload)
```

### Modules

| File | Responsibility |
|---|---|
| `config.py` | Pydantic-settings singleton; validates all env vars at startup |
| `schemas.py` | All Pydantic v2 models + LangGraph TypedDict state schema |
| `browser_utils.py` | Stealth Playwright browser pool, anti-bot evasion, jitter delays |
| `signals_mcp_server.py` | FastMCP server with 4 scraping tools |
| `context_refiner.py` | Azure OpenAI synthesis layer (temperature=0, json_object mode) |
| `graph_orchestrator.py` | LangGraph StateGraph — 3 nodes, compiled graph singleton |
| `main.py` | CLI entry point; lead loading, parallel dispatch, output writing |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set AZURE_OPENAI_API_KEY at minimum
```

### 3. Run

```bash
# Process the sample leads (Rippling, Deel, Chick-fil-A, Shake Shack)
python main.py

# Custom leads file + concurrency
python main.py --leads my_leads.json --concurrency 3
```

Output files are written to `./outputs/output_{domain}.json`.

---

## ICP Routing Logic

| Signal | SaaS ICP (CHRO 200-500 emp.) | Restaurant ICP (HR/Recruitment Dir.) |
|---|---|---|
| Funding rounds | ✅ `fetch_funding_signal` | ❌ skipped |
| Store expansion | ❌ skipped | ✅ `fetch_operational_expansion` |
| ATS telemetry | ✅ always | ✅ always |
| Tech stack | ✅ always | ✅ always |

---

## Output Schema

```json
{
  "signal_category": "saas | restaurant",
  "target_domain": "example.com",
  "extraction_timestamp": "2025-01-15T18:30:00+00:00",
  "signal_data": {
    "financial_event_detected": true,
    "latest_growth_metric": "Series B $85M raised Q4 2024",
    "expansion_detected": false,
    "expansion_summary": "",
    "is_actively_hiring": true,
    "total_open_requisitions": 47,
    "critical_roles_identified": ["VP of People", "HRBP - EMEA"],
    "detected_tech_stack": ["Workday", "Lattice"]
  },
  "llm_metadata": {
    "confidence_score": 0.87,
    "context_ready_summary": "Rippling closed a Series B..."
  }
}
```

---

## Anti-Bot Evasion

- **5-UA pool** rotated per page context (Chrome 123/124, Edge, Firefox)
- **Randomised viewport** (1280–1920 × 800–1080)
- **Stealth JS injection**: removes `navigator.webdriver`, spoofs `navigator.plugins`, overrides permission query
- **Jittered delays**: `base_delay + uniform(0, max_jitter)` between all requests
- **Retry logic**: `safe_goto()` retries up to 3× with backoff on navigation failure

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | — | **Required** Azure API key |
| `AZURE_OPENAI_ENDPOINT` | `https://moodbit-gpt-useast2.openai.azure.com/` | Azure resource URL |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.5` | Deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | API version |
| `MAX_CONCURRENT_TOOLS` | `4` | Parallel leads cap |
| `BASE_REQUEST_DELAY_SECONDS` | `1.5` | Base sleep between requests |
| `MAX_JITTER_SECONDS` | `2.0` | Max random jitter |
| `BROWSER_HEADLESS` | `true` | Headless mode |
| `BROWSER_TIMEOUT_MS` | `30000` | Navigation timeout |
| `OUTPUT_DIR` | `./outputs` | Output directory |
