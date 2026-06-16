# 🎯 ICP Search Engine & Lead Enrichment Pipeline

A **generic, company-agnostic** intelligence engine that takes any company name and automatically processes it through a stateful research pipeline. It produces a structured Ideal Customer Profile (ICP) assessment — evaluating corporate identity, financial scale, and real-time growth signals.

Built using **LangGraph + Azure OpenAI** with a pluggable search backend (Serper API or Playwright), this pipeline is designed to automate B2B target research at scale, replacing hours of manual googling with seconds of structured agentic execution.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# → Fill in AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
# → Add SERPER_API_KEY (free at https://serper.dev — 2,500 searches/month)

# 3. Add your target companies
nano companies.txt   # Add one company name per line

# 4. Run the pipeline
python main.py
```

---

## 🏗️ How it Works & Core Flow

Rather than using basic, flat web scraping, the engine utilizes a stateful graph powered by **LangGraph**. Every company is processed independently through a structured pipeline of nodes, making decisions based on previously gathered context to ensure extreme accuracy.

```
INPUT: Company Name(s) (with auto-deduplication)
       ↓
 ┌─────────────────────────────────────────────────────────────┐
 │                     LangGraph Pipeline                      │
 │                                                             │
 │  Step 1: Identity & Digital Presence                         │
 │  • Resolves website and LinkedIn URLs                       │
 │  • Captures core industry sector and description            │
 │                             ↓                               │
 │  Step 2: Scale & Financial Health                           │
 │  • Detects tickers, market cap, and funding stages          │
 │  • Estimates customer volumes & corporate scale             │
 │                             ↓                               │
 │  Step 3: Growth & Intention Signals                         │
 │  • Scans 10+ ATS platforms (Workday, Taleo, etc.)           │
 │  • Extracts expansion news and hiring milestones            │
 │                             ↓                               │
 │  Step 4: AI Synthesis & ICP Assessment                      │
 │  • Computes fit score (1-10) and next action                │
 │  • Formulates a concise 3-5 sentence brief                  │
 └─────────────────────────────────────────────────────────────┘
       ↓                                 ↓
  [Google Search / HTTP]            [Azure OpenAI]
  - Serper API (Prod)               - Structure normalization
  - Playwright (Fallback)           - Disambiguation filtering
       ↓
OUTPUTS:
 ├── 📊 Summary CSV (Clean, N/A-formatted CRM-ready table)
 └── 📄 Detailed CSV (Full raw HTML & prompt audit log)
```

---

## 📋 Detailed Pipeline Phases

### Phase 1 — Identity & Digital Presence
This phase anchors the company's real digital footprint to prevent downstream search collisions.
* **Step 1.1 (Official URL):** Search query `"{company} official website"`. Resolves the exact homepage URL.
* **Step 1.2 (LinkedIn Profile):** Search query `site:linkedin.com/company "{company}"`. Extracts official description, industry category, and employee count.

### Phase 2 — Scale & Financial Health
Determines the size, funding class, and budget of the organization.
* **Step 2.1 (Public Markets):** Search query `"{company}" (ticker OR "market cap" OR site:finance.yahoo.com OR site:marketwatch.com)`. Extracts ticker symbol, exchange, and market capitalization.
* **Step 2.2 (Capital & Revenue):** Search query `"{company}" (crunchbase OR "funding round" OR "series A" OR "annual revenue" OR "Q3 results" OR "earnings report" OR "raised")`. Captures funding stages and revenue performance.
* **Step 2.3 (Customer Footprint):** Search query `site:{domain} ("trusted by" OR "customers" OR "teams relying on" OR "clients" OR "case studies" OR "success stories" OR "investor relations" OR "annual report")`. Estimates team scale and client count.

### Phase 3 — Hiring & Growth Signals
Detects active expansion milestones indicating high outbound conversion potential.
* **Step 3.1 (ATS Telemetry):** Search query scanning `greenhouse.io`, `boards.greenhouse.io`, `jobs.lever.co`, `*.myworkdayjobs.com`, `jobs.taleo.net`, `icims.com`, `careers.smartrecruiters.com`, `successfactors.com`, `wellfound.com`, and `ashbyhq.com` for active hiring.
* **Step 3.2 (Expansion News):** Search query `"{company}" (expansion OR "new office" OR "new market" OR "new headquarters" OR hiring OR "growing team" OR series OR acqui) after:2024-01-01`.

### Phase 4 — Synthesis
Our senior analyst prompt evaluates all collected scale and signal metrics to assign an ICP fit score (1–10), recommend a next step (`reach_out`, `monitor`, `skip`, or `research_more`), and generate a target-specific assessment brief.

---

## ⚡ Enterprise Resilience & Advanced Reasoning

Built to run reliably under real-world data constraints:

### 🛡️ Infinite-Hang Prevention & Error Recovery
* **Dual-Layer Timeouts:** Individual search requests are capped at 15 seconds. The entire search execution layer is wrapped in a hard `asyncio.wait_for` timeout of 30 seconds to bypass unresponsive search endpoints.
* **Resilient Nodes:** Every LangGraph node runs inside isolated try-except blocks. If a step fails, it prints/logs the error, initializes defaults (like `is_public: false` or empty datasets), and continues the pipeline instead of terminating the process.
* **Azure Content Filter Bypass:** The pipeline explicitly intercepts `openai.BadRequestError` exceptions. If Azure's content filter flags raw search HTML, it logs a warning and skips that step, continuing without losing the run.

### 🧠 Entity Disambiguation & Smart Reasoning
* **Name-Collision Filtering:** Prevents the agent from pulling news for unrelated local businesses sharing a brand name (e.g., retrieving a local gym's news instead of Asana the software). The sector and official URL discovered in Phase 1 are cross-referenced in all subsequent prompts.
* **Ticker Cross-Referencing:** Discards mismatching tickers (e.g., discarding a Japanese Tokyo Stock Exchange ticker when evaluating a US software company of the same name).
* **Corporate Hierarchy Support:** Captures hiring signals even if listed under a subsidiary, franchise, or parent entity (e.g., counting "Marriott Vacations Worldwide" jobs towards Marriott International).
* **ABS-Based Scale Inference:** In the absence of direct employee counts, the synthesis engine infers scale from funding/cap values (e.g., funding >$100M or market cap >$1B automatically elevates the class to at least `mid_market` or `enterprise`).

---

## 📁 Output Files

Two CSVs are written to `./outputs/` per execution:

### 1. `company_summary_YYYYMMDD_HHMM.csv`
The final deliverable, optimized for direct import into CRM systems (HubSpot, Salesforce).
* **No Blank Cells:** All missing optional values default to `"N/A"`.
* **Private Company Sentinel:** Empty ticker and market cap values are automatically written as `"Private"` to prevent confusing layout spaces.

| Column | Description |
|--------|-------------|
| `company_name` | Company name |
| `official_url` | Official website (defaults to `"N/A"`) |
| `linkedin_url` | LinkedIn company page URL (defaults to `"N/A"`) |
| `industry` | LinkedIn industry sector (defaults to `"N/A"`) |
| `description` | Corporate bio description (defaults to `"N/A"`) |
| `company_size` | `startup` / `smb` / `mid_market` / `enterprise` |
| `funding_stage` | `seed` / `series_a` / `public` / etc. |
| `total_funding` | Total raised capital (defaults to `"N/A"`) |
| `stock_ticker` | Ticker symbol (defaults to `"Private"`) |
| `market_cap` | Market capitalization (defaults to `"Private"`) |
| `customer_count_estimate` | Estimated client/team scale (defaults to `"N/A"`) |
| `growth_signals` | Pipe-separated signals (e.g. `hiring, expanding` or `"N/A"`) |
| `active_job_count` | Number of open roles found |
| `active_job_titles` | Pipe-separated list of top 20 active job listings (defaults to `"N/A"`) |
| `expansion_news` | Pipe-separated list of top 5 expansion headlines (defaults to `"N/A"`) |
| `analyst_summary` | LLM-generated ICP assessment (defaults to `"N/A"`) |
| `processing_errors` | Any errors encountered during the run (defaults to `"None"`) |
| `detail_log_ref` | Filename of the detailed search log for auditing |
| `processed_at` | UTC ISO timestamp |

### 2. `detailed_search_log_YYYYMMDD_HHMM.csv`
A full audit log recording every search step. Ideal for debugging queries or evaluating LLM extraction accuracy.

| Column | Description |
|--------|-------------|
| `company_name` | Company being researched |
| `phase` | e.g. `1.1_official_url`, `3.1_job_postings` |
| `query_or_url` | Exact search query or URL used |
| `backend_used` | `serper` or `playwright` |
| `success` | Whether the search succeeded |
| `error_message` | Error details if the step failed |
| `extracted_data_json` | JSON string of what the LLM extracted |
| `raw_html_snippet` | First 800 chars of page HTML / result text |
| `timestamp` | UTC ISO timestamp |

---

## 🔧 CLI Options

```bash
python main.py                              # Processes companies.txt
python main.py --company "Moodbit"          # Research a single company
python main.py --companies "Salesforce,A"   # Research a comma-separated list
python main.py --file Custom_leads.txt      # Specify custom input path
python main.py --backend playwright         # Force headless browser search
python main.py --max-concurrent 3           # Adjust execution speed/parallelism
```

---

## 🗂️ File Structure

```
emails_icp/
├── main.py               # CLI parser, deduplicator & orchestrator
├── graph.py              # LangGraph pipeline definition
├── pipeline_phases.py    # Logic, queries, & LLM prompts for Phase 1-4
├── search_tools.py       # Pluggable buscar() search abstraction
├── csv_writer.py         # CSV serialization with clean N/A / Private formatting
├── schemas.py            # Pydantic models enforcing structural typing
├── config.py             # Configuration validation and environment loader
├── companies.txt         # Input company names list
├── requirements.txt      # Python dependencies
└── outputs/              # Destination folder for generated reports
```
