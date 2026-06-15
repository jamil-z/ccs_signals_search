# 📄 Technical Documentation: Signal Extraction — Data Sources & Methodologies

**Target ICP:** Fast-Food Chains, Quick Service Restaurants (QSR), and Franchisees  
_(e.g., McDonald's, Taco Bell, Tacos 1986)_

**Objective:** Define the exact digital locations (**Where**) and the programmatic methods (**How**) to extract deterministic buying signals without relying on LLM hallucination.

---

## Signal 1 — Geographic & Footprint Expansion

> **Indicator:** The restaurant is actively opening new physical locations, triggering localized mass-hiring needs.

### Where to Get the Data

- **Corporate Store Locators and Interactive Maps**
  - Examples: `tacobell.com/locations`, `starbucks.com/store-locator`
  
> **Key Insight:** The data is *never* in the static HTML. Modern chains use **Mapbox**, **Google Maps APIs**, or custom **GraphQL endpoints** to load store coordinates asynchronously. Any naive HTML scraper will return zero results.

---

### How to Extract It

**Methodology: XHR / Fetch Interception**

We do not scrape the visual webpage. Instead, using **Playwright** (in headless mode), we intercept the browser's network layer to isolate the hidden backend API calls that serve store data.

**Target endpoint patterns:**
| Pattern | Example |
|---|---|
| JSON file | `/stores.json` |
| REST API | `/api/v1/stores` |
| GraphQL | `/graphql` (POST body inspection) |
| Boundary param | `?boundary=33.4,-117.9,34.0,-118.5` |

**Delta Comparison Logic (the core signal):**

```
Day 1 → Script intercepts JSON payload → saves array of Store_IDs to DB
         Example: 5,000 McDonald's locations stored.

Day N → Script runs again → new payload contains 5,005 locations.
         Delta = {store_id_5001, store_id_5002, store_id_5003, store_id_5004, store_id_5005}

Result → Net-new Store_IDs mathematically prove an expansion event.
```

> **Why this is deterministic:** The appearance of a net-new `Store_ID` is not inferred — it is a mathematical fact derived from set subtraction: `new_set - old_set`.

---

## Signal 2 — Frontline Churn & High-Velocity Hiring

> **Indicator:** The restaurant is suffering from high employee turnover (churn) or is desperately trying to staff a new location, indicating an immediate need for HR/Recruitment software.

### Where to Get the Data

Fast-food chains rarely use standard B2B ATS platforms like Ashby or Greenhouse. They rely on **hospitality-specific, high-volume platforms**:

| Platform | Notes |
|---|---|
| **Harri** | Dominant in hospitality / QSR |
| **Snagajob** | Hourly worker marketplace |
| **Paradox.ai / Olivia** | Used by McDonald's and Wendy's for automated SMS recruiting |
| **Workday** | For corporate and large franchise management |

---

### How to Extract It

**Step 1 — Subdomain Enumeration & Redirect Analysis**

The system pings `{company_domain}/careers` and follows all HTTP redirects. The final destination URL reveals the ATS platform in use.

```
GET https://mcdonalds.com/careers
→ 302 → https://mcdonalds.paradox.ai/apply
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
         ATS = Paradox.ai ✓
```

**Step 2 — Job Density Analysis**

Once the ATS endpoint is identified, the script queries the platform's hidden JSON endpoint to retrieve all active job listings for that company.

**Step 3 — Deduplication & Churn Detection**

Each job posting is stored with a hash of its key fields (`role_title + store_id + posting_date`). The system tracks lifecycle events:

```
Posting lifecycle:
  2025-06-01  → "Shift Supervisor - Store #142"  [CREATED]
  2025-06-10  → "Shift Supervisor - Store #142"  [REMOVED]
  2025-06-25  → "Shift Supervisor - Store #142"  [RE-POSTED]
               ↳ Gap: 15 days → Tag: HIGH_CHURN_ANOMALY 🔴
```

> **Signal strength:** A role re-posted within 30 days of removal on the same store indicates an inability to retain talent — a strong trigger for HR/workforce management solutions.

---

## Signal 3 — Franchisee Consolidation & Corporate Moves

> **Indicator:** A regional franchise owner is acquiring competitors or opening multiple locations simultaneously, requiring a unified HR compliance system.

### Where to Get the Data

Industry-specific trade publications and localized business journals:

| Source | Type |
|---|---|
| **QSR Magazine** (`qsrmagazine.com`) | Industry trade publication |
| **Franchise Times** (`franchisetimes.com`) | M&A and expansion news |
| **Nation's Restaurant News** (`nrn.com`) | Operator news |
| **PR Newswire** (regional feeds) | Corporate press releases |

---

### How to Extract It

**Methodology: Automated OSINT via Google Search Operators**

We bypass the need to scrape hundreds of individual news sites by using **advanced search engine operators** executed programmatically via the Google Custom Search API or Serper API.

**Query template:**
```
intext:"acquires" OR intext:"new locations" "{COMPANY_NAME}" -yelp -tripadvisor -doordash -ubereats -grubhub
```

**Exclusion rules — mandatory filters:**

Using the `-` operator is non-negotiable. Without it, the system would parse thousands of consumer review pages and food delivery listings instead of B2B corporate news.

| Excluded domain | Reason |
|---|---|
| `-yelp` | Consumer reviews, not corporate news |
| `-tripadvisor` | Consumer reviews |
| `-doordash` | Food delivery, not expansion news |
| `-ubereats` | Food delivery |
| `-grubhub` | Food delivery |

**Parsing:** The script extracts the resulting headlines, publication dates, and source domains. Only text fragments containing the trigger keywords are forwarded to the LangGraph state — the LLM never sees raw, unfiltered HTML.

---

## 🗺️ Data Flow Architecture Summary

```
┌─────────────────────────────────────────────────────────┐
│  INPUT: Company name read from lead list (TXT / CSV)    │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │  ICP Router             │
          │  (domain heuristics)    │
          └────┬──────────────┬─────┘
               │              │
    ┌──────────▼──┐     ┌─────▼──────────────┐
    │ ATS Scraper │     │ Store Locator XHR   │
    │ (Paradox,   │     │ (JSON delta check)  │
    │  Harri, etc)│     └─────────────────────┘
    └──────────┬──┘
               │
    ┌──────────▼──────────────┐
    │  OSINT Engine           │
    │  (Google Dork queries)  │
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │  Verification Layer     │
    │  - Delta comparison     │
    │  - Churn detection      │
    │  - Deduplication cache  │
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │  LLM Synthesis (GPT-5.5)│
    │  Only receives verified │
    │  facts, not raw HTML    │
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐
    │  OUTPUT: results.csv    │
    │  (one row per company)  │
    └─────────────────────────┘
```

| Step | Action |
|---|---|
| **1. Input** | Read `McDonald's` from the lead list file |
| **2. Routing** | System targets ATS (`paradox.ai`) for hiring data and XHR endpoints (`/api/v1/restaurants`) for store data |
| **3. Extraction** | Raw JSON payloads and OSINT headlines are pulled silently |
| **4. Verification** | System compares today's store count vs. yesterday's; checks for churn anomalies |
| **5. Synthesis** | Verified facts (e.g., _"Opened 3 stores in Texas"_, _"High churn on Shift Managers"_) are passed to the LLM in a strict schema |
| **6. Output** | Final structured row appended to `results.csv` |
