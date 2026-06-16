# 🎯 Lead Enrichment & Signal Extraction Pipeline (Automotive Design Michigan ICP)

This commercial intelligence engine is hyper-targeted to identify, evaluate, and qualify companies involved in **automotive design, transportation design, concept engineering, prototyping, and mobility innovation in Michigan** (specifically the Detroit metro area).

Built using **LangGraph + Azure OpenAI** with a pluggable search backend (Serper API or Playwright), this pipeline automates B2B target research at scale, evaluating target profiles against **8 key design signals** to assign an Ideal Customer Profile (ICP) fit score (1–10) and recommend personalized outbound actions.

---

## 🚀 Quick Start

```bash
# 1. Activate your virtual environment and install dependencies
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure environment variables (.env)
# Copy .env.example to .env and define:
# - AZURE_OPENAI_API_KEY & AZURE_OPENAI_ENDPOINT
# - SERPER_API_KEY (for Google search queries)
# - MAX_CONCURRENT_COMPANIES (concurrency control, e.g., 2 or 5)

# 3. Define target companies
# Update the 'companies.txt' file, adding one company name per line.
# Example:
# Ford Motor Company
# Sundberg-Ferar
# Prefix Corporation
# The Shyft Group

# 4. Run the pipeline
python main.py
```

---

## 🏗️ Stateful Graph Topology (5 Phases)

Instead of using flat, linear scraping, the engine utilizes a stateful graph powered by **LangGraph**. Each company is processed through five distinct research phases:

```
INPUT: Company Name(s) (with automatic CLI deduplication)
       ↓
 ┌─────────────────────────────────────────────────────────────┐
 │                     LangGraph Pipeline                      │
 │                                                             │
 │  Phase 1: Identity & Digital Presence                       │
 │  • Resolves official domain and LinkedIn URLs               │
 │  • Captures industry sector and description                 │
 │                             ↓                               │
 │  Phase 2: Scale & Financial Health                          │
 │  • Aggressive public market check (Tickers, Market Cap)     │
 │  • Identifies funding stages and revenue metrics            │
 │                             ↓                               │
 │  Phase 3: Hiring & General Growth Signals                   │
 │  • Scans 10+ ATS platforms (Greenhouse, Workday, etc.)      │
 │  • Extracts expansion news and general growth indicators    │
 │                             ↓                               │
 │  Phase 4: Automotive Design Signals (8 New Signals)         │
 │  • Extracts 8 hyper-specific design signals & evidence      │
 │                             ↓                               │
 │  Phase 5: Strategic Synthesis (Design Strategist LLM)       │
 │  • Computes ICP score (1-10) and outreach action            │
 │  • Formulates a concise strategic target assessment         │
 └─────────────────────────────────────────────────────────────┘
       ↓                                 ↓
 [Serper / Playwright]            [Azure OpenAI]
 - Agnostic search layer          - Pydantic-based extraction
 - Asynchronous timeouts          - Disambiguation filtering
       ↓
OUTPUTS (outputs/ directory):
 ├── 📊 Summary CSV (Detailed 8-signal schema, CRM-ready)
 └── 📄 Detailed CSV (Raw HTML snippets & query execution log)
```

---

## 🔍 The 8 Target Design Signals (Phase 4)

The pipeline searches, extracts, and logs evidence for the following signals:

1. **Internal Creative Team (Signal 1):** Does the company have an internal creative/design team or studio in Michigan? (e.g., GM Design Center in Warren).
2. **Creative Support Need (Signal 2):** Do they require external creative support, design consultancies, or CMF specialists?
3. **Creative Tech Stack (Signal 3):** Core software tools used. Targeted keywords: *Adobe, Autodesk Alias, Autodesk VRED, CATIA, SolidWorks, Unity, Unreal Engine, ZBrush, Rhino*.
4. **Enterprise Software Budget (Signal 4):** Indicators of software license purchase capability (e.g., job postings requiring enterprise tool administration).
5. **Hiring Creative Roles (Signal 5):** Active job postings for roles like transportation designers, CMF designers, Alias modelers, or 3D visualization artists.
6. **Upskilling Programs (Signal 6):** Technical development programs, workforce training, or community college partnerships.
7. **Creative Leadership (Signal 7):** Executive or leadership roles in design (e.g., *Chief Design Officer*, *VP of Design*, *Creative Director*).
8. **Local Michigan Involvement (Signal 8):** Recipient of MEDC grants, partner in regional workforce alliances, or member of Michigan associations.

---

## 📊 CSV Report Schemas

Two CSV files are generated under `./outputs/` per execution:

### 1. Account Summary Table (`company_summary_YYYYMMDD_HHMM.csv`)
An CRM-ready table (empty cells default to `"N/A"` or `"No"`).

| Column Group | CSV Field | Type | Description |
|---|---|---|---|
| **Identity** | `company_name` | String | Name of the researched company |
| | `official_url` | String / `N/A` | Home page URL |
| | `linkedin_url` | String / `N/A` | Official LinkedIn company profile URL |
| | `industry` | String / `N/A` | Sector category (e.g., *Motor Vehicle Manufacturing*) |
| | `description` | String / `N/A` | Brief company overview (max 200 chars) |
| **Scale & Finance** | `company_size` | Enum / `unknown` | `startup` \| `smb` \| `mid_market` \| `enterprise` |
| | `employee_count_estimate` | String / `N/A` | Employee size range from LinkedIn |
| | `funding_stage` | Enum / `unknown` | `public` \| `seed` \| `series_a` \| `bootstrapped` \| `unknown` |
| | `total_funding` | String / `N/A` | Total capital raised |
| | `stock_ticker` | String / `Private` | Public trading ticker (e.g., `SHYF`) |
| | `market_cap` | String / `Private` | Market capitalization |
| | `customer_count_estimate` | String / `N/A` | Estimated client/user footprint |
| **General Growth** | `growth_signals` | Pipe-separated | Core growth events (e.g., `expanding \| hiring`) |
| | `active_job_count` | Integer | Total open job postings detected |
| | `active_job_titles` | Pipe-separated | Titles of open positions (up to 20) |
| | `expansion_news` | Pipe-separated | Headlines of recent expansion news (up to 5) |
| **8 Design Signals** | `sig1_has_internal_creative_team` | `Yes` / `No` | Signal 1: Has an internal design studio? |
| | `sig1_internal_creative_team_evidence` | String / `N/A` | Evidence details for Signal 1 |
| | `sig2_requires_creative_support` | `Yes` / `No` | Signal 2: Requires design consulting/outsourcing? |
| | `sig2_creative_support_evidence` | String / `N/A` | Evidence details for Signal 2 |
| | `sig3_detected_creative_tools` | Pipe-separated | Signal 3: Detected tools (e.g. `Autodesk Alias \| CATIA`) |
| | `sig3_tech_stack_evidence` | String / `N/A` | Evidence details for Signal 3 |
| | `sig4_has_enterprise_software_budget` | `Yes` / `No` | Signal 4: Enterprise software purchase indicators? |
| | `sig4_budget_evidence` | String / `N/A` | Evidence details for Signal 4 |
| | `sig5_is_hiring_creative_roles` | `Yes` / `No` | Signal 5: Actively recruiting creative designers? |
| | `sig5_creative_job_titles` | Pipe-separated | Job titles for open creative positions |
| | `sig5_hiring_evidence` | String / `N/A` | Evidence details for Signal 5 |
| | `sig6_offers_upskilling` | `Yes` / `No` | Signal 6: Upskilling or professional training? |
| | `sig6_upskilling_programs` | Pipe-separated | Names of professional training programs |
| | `sig6_upskilling_evidence` | String / `N/A` | Evidence details for Signal 6 |
| | `sig7_has_creative_leadership` | `Yes` / `No` | Signal 7: Executive design leadership present? |
| | `sig7_creative_leadership_titles` | Pipe-separated | Leadership roles found (e.g. `VP of Design`) |
| | `sig7_leadership_evidence` | String / `N/A` | Evidence details for Signal 7 |
| | `sig8_has_michigan_local_involvement` | `Yes` / `No` | Signal 8: Michigan local grants/MEDC/alliances? |
| | `sig8_michigan_involvement_details` | Pipe-separated | Local involvement milestones and partners |
| | `sig8_michigan_involvement_evidence` | String / `N/A` | Evidence details for Signal 8 |
| **Synthesis** | `icp_score` | Integer `1-10` | Qualified fitness score |
| | `recommended_action` | Enum | `reach_out` \| `monitor` \| `skip` \| `research_more` |
| | `key_buying_signals` | Pipe-separated | Buying triggers detected |
| | `analyst_summary` | String / `N/A` | Qualitative brief from Design Strategist LLM |
| **Metadata** | `processing_errors` | Pipe-separated | List of processing errors (defaults to `None`) |
| | `detail_log_ref` | String | Filename of detailed search log for auditing |
| | `processed_at` | Timestamp | ISO UTC completion time |

### 2. Search Execution Log (`detailed_search_log_YYYYMMDD_HHMM.csv`)
An audit trail detailing the query strings executed, APIs called (Serper or Playwright), response status, and first 800 characters of the raw search result HTML snippets for full reproducibility.

---

## 🔧 Command Line Options

```bash
# Analyze all targets in companies.txt
python main.py

# Process a single target company
python main.py --company "Prefix Corporation"

# Process multiple comma-separated companies
python main.py --companies "Sundberg-Ferar,Middlecott,Italdesign USA"

# Override search backend to Playwright
python main.py --backend playwright

# Set max parallel executions
python main.py --max-concurrent 4
```

---

## 🛡️ Enterprise Engineering & Disambiguation

* **Ticker Extraction**: Custom Prompt instructions enforce aggressive extraction of tickers (such as `SHYF`) and market caps from conversational text snippets.
* **Content Filter Interception**: Catches `openai.BadRequestError` exceptions triggered by raw search HTML snippets, replacing them with safe defaults to prevent graph interruption.
* **Target Isolation**: Evaluates discovered industries (e.g. *Motor Vehicle Manufacturing*) and URLs to filter out generic businesses sharing target names.
