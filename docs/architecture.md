# NHA SHA Analytical Co-pilot — System Architecture

> ⚠️ **SUPERSEDED / HISTORICAL.** This document describes the original **PM-JAY
> claims (TMS/BIS)** design. The project has since been migrated to the **ABDM
> digital-adoption** domain (9 tables, no merged table). The architecture *shape*
> (React → FastAPI → BigQuery, three-layer SQL safety, RBAC, NL-to-SQL via a
> governance prompt) still holds, but the **data model, tables, columns, and
> domain rules here are out of date**. For the current, authoritative schema and
> rules see **`backend/GOVERNANCE.md`**, and for setup/usage see the top-level
> **`README.md`**. Kept for design-rationale history only.

**Version:** 0.3 (Prototype Scope — PM-JAY era, superseded)
**Audience:** Developer
**Status:** Design specification — historical
**Change from v0.1:** Core intelligence layer revised from parameterised function library to free-form NL-to-SQL generation. Rationale documented in Section 4.3.
**Change from v0.2:** Section 7 rewritten with the actual generated structure of both synthetic tables (real column counts, join-key format, repeat-visit design, geographic scope asymmetry between the two tables). New Section 7.4 documents data quality quirks deliberately preserved from the real source files. Section 4.6 and Section 13 updated to reflect what is now known versus still assumed.

---

## 1. Overview

The SHA Analytical Co-pilot is a web-based chat interface that allows NHA, SHA, and HAAU
officials to query PM-JAY claims and beneficiary data using natural language. The system
translates natural language inputs directly into SQL using an LLM — enabling open-ended
exploratory analysis, not just pre-defined reports.

The tool is designed for officials who have deep domain knowledge but limited data skills.
Its primary value is enabling analysis that currently cannot happen due to a shortage of
trained analytical staff in government offices. This means the tool must answer questions
that nobody has anticipated in advance, not just questions from a fixed menu.

This document covers the prototype architecture, scoped to synthetic data mirroring one
real NHA financial year. The prototype architecture is the production architecture — no
structural rework is required to move to production, only data substitution, infrastructure
scaling, and GOVERNANCE.md refinement.

---

## 2. Design Principles

| Principle | Rationale |
|---|---|
| Free-form NL-to-SQL over parameterised functions | Officials need exploratory analysis, not just recurring reports. Unanticipated questions must be answerable. |
| GOVERNANCE.md as the governance layer | All domain rules, data quality caveats, business definitions, and SQL conventions are encoded in GOVERNANCE.md. The quality of the tool is directly proportional to the quality of GOVERNANCE.md. |
| SQL transparency by default | Every response includes the SQL used, in a collapsible field. This is the audit trail. |
| Fail-safe on SQL errors | If generated SQL fails at execution, the system catches the error, tells the user it could not answer, and logs the failure for GOVERNANCE.md improvement. No automatic retry. |
| Read-only enforcement at two independent layers | System prompt prohibits write SQL. PostgreSQL read-only role enforces it at the database level regardless of what the LLM generates. |
| Indian data residency | All model inference and data storage must remain on-premise or within approved Indian cloud infrastructure. LLM interface is abstracted for model swapping. |
| Role-based access control (RBAC) | Officials see only the data their role permits. Enforced at the backend before SQL is executed. |
| Prototype is production architecture | Every design decision made for the prototype must be valid at production scale. No shortcuts that require structural rework later. |

---

## 3. System Components

```
┌─────────────────────────────────────────────────────────┐
│                    User (Browser)                       │
│              Web Chat Interface (React)                 │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Answer (text + table/chart)                    │   │
│  │  ▼ View SQL used to generate this insight       │   │
│  │    [collapsible SQL block]                      │   │
│  └─────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────┐
│                   API Gateway / Backend                 │
│                   (FastAPI / Python)                    │
│                                                         │
│  ┌─────────────────┐      ┌──────────────────────────┐  │
│  │  Auth & RBAC    │      │   Session / Context Mgr  │  │
│  │  (JWT + roles)  │      │   (multi-turn dialogue)  │  │
│  └────────┬────────┘      └──────────────┬───────────┘  │
│           │                              │               │
│  ┌────────▼──────────────────────────────▼───────────┐  │
│  │               NL-to-SQL Layer                     │  │
│  │   LLM (on-premise / Indian-compliant endpoint)    │  │
│  │   GOVERNANCE.md → system prompt                       │  │
│  │   Generates SQL from natural language input       │  │
│  │   Clarifying dialogue when query is ambiguous     │  │
│  └────────────────────────┬──────────────────────────┘  │
│                           │                              │
│  ┌────────────────────────▼──────────────────────────┐  │
│  │             SQL Validation & Safety Layer          │  │
│  │   Reject any non-SELECT statement                 │  │
│  │   RBAC filter: inject role-based WHERE clauses    │  │
│  │   Execute against read-only DB connection         │  │
│  │   On error: catch, log, return failure message    │  │
│  └────────────────────────┬──────────────────────────┘  │
│                           │                              │
│  ┌────────────────────────▼──────────────────────────┐  │
│  │             Semantic / Geography Layer             │  │
│  │   LGD code resolution, post-2011 district splits  │  │
│  │   HBP 2.2 specialty taxonomy                      │  │
│  │   Indicator synonym mapping                       │  │
│  └────────────────────────┬──────────────────────────┘  │
└───────────────────────────┼─────────────────────────────┘
                            │ Read-only SQL (SELECT only)
┌───────────────────────────▼─────────────────────────────┐
│                    Data Layer                           │
│              PostgreSQL (prototype)                     │
│   Synthetic PM-JAY + BIS data — one financial year     │
│   Read-only role enforced at DB level                  │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Component Specifications

### 4.1 Web Chat Interface

**Stack:** React (TypeScript), served as a static build.

**Features:**
- Single-page chat UI with message history
- Renders structured responses: text first, then table or chart alongside
- Every response includes a collapsible SQL field labelled
  "View query used to generate this insight" — present by default, not visually dominant
- Displays active context as chips (e.g. `State: Karnataka | Period: Q2 2023-24`)
- Session persistence within browser tab (no cross-session memory in prototype)
- Login screen with username/password → JWT issued by backend

**Response layout (per message):**
```
┌─────────────────────────────────────────────┐
│ [Answer in plain text]                      │
│                                             │
│ [Table or chart if data warrants it]        │
│                                             │
│ ▼ View query used to generate this insight  │
│   SELECT ...                                │
│   FROM ...                                  │
└─────────────────────────────────────────────┘
```

**Out of scope for prototype:** file export, saved queries, dashboards, cross-session memory.

---

### 4.2 API Gateway / Backend

**Stack:** Python, FastAPI.

**Responsibilities:**
- Receive chat messages from frontend
- Validate JWT, extract user role
- Maintain session context (conversation history, active geography/period state)
- Route to NL-to-SQL layer
- Validate and execute generated SQL
- Log all queries and any execution errors
- Return structured response (answer text + result set + SQL) to frontend

**Endpoints (prototype):**

| Endpoint | Method | Description |
|---|---|---|
| `/auth/login` | POST | Issue JWT on valid credentials |
| `/chat/message` | POST | Main chat turn handler |
| `/chat/session/{id}` | GET | Retrieve session history |
| `/query-log` | GET | Admin: retrieve query execution log |
| `/health` | GET | Liveness check |

**Read-only enforcement (Layer 1):** The backend uses a PostgreSQL connection string bound
to a read-only role. No `INSERT`, `UPDATE`, `DELETE`, or `DDL` is ever issued from the
application layer. This is enforced at the connection level, not just in application logic.

---

### 4.3 NL-to-SQL Layer

This is the core intelligence layer. The LLM receives the full GOVERNANCE.md as its system
prompt and generates SQL directly from natural language input.

**Why free-form SQL generation over a parameterised function library:**

The tool is designed for exploratory analysis by officials who need to ask questions that
nobody has anticipated in advance. A parameterised function library can only answer
questions it was pre-programmed to answer — it cannot handle novel analytical questions.
Free-form SQL generation with a strong GOVERNANCE.md governance layer enables open-ended
exploration while maintaining correctness through explicit domain rules, data quality
caveats, and SQL conventions encoded in the system prompt.

The tradeoff is accepted: the same question may produce slightly different SQL on different
runs. This is mitigated by SQL transparency (every query is shown to the user), query
logging (all SQL is logged for review), and continuous GOVERNANCE.md refinement (errors and
edge cases are encoded as rules). Auditability is achieved through the query log, not
through a fixed function definition.

**Model choice (prototype):** Any OpenAI-compatible endpoint that can be self-hosted or
run on an Indian-compliant cloud (e.g., Llama 3 8B via Ollama, or a hosted model on an
approved Indian cloud provider). The LLM client is abstracted behind a single interface
so the model can be swapped without changes to application logic.

**Processing pipeline per turn:**

```
User message
    │
    ▼
1. Geography / period resolution
   → Resolve any place names to LGD codes
   → Resolve any time references to date ranges
   → If ambiguous (e.g. two districts with same name) → ask one clarifying question
    │
    ▼
2. Context injection
   → Inject session context (confirmed geography, period, prior filters)
   → Inject GOVERNANCE.md as system prompt
    │
    ▼
3. SQL generation
   → LLM generates SQL against the schema in GOVERNANCE.md
   → LLM also generates a plain-language answer template
    │
    ▼
4. SQL safety check
   → Parse generated SQL
   → If any non-SELECT statement detected → reject, return error to user, log
   → If SELECT → proceed
    │
    ▼
5. RBAC enforcement
   → Inject role-based filters (e.g. viewer role cannot query hospital-level data)
   → Modify WHERE clause as needed before execution
    │
    ▼
6. Execution
   → Run SQL against read-only PostgreSQL connection
   → On success → return result set
   → On error → catch exception, log full details (query + error + session context),
     return "I was unable to answer that question" message to user
     Do NOT retry automatically
    │
    ▼
7. Response formatting
   → LLM formats result set into plain-language answer
   → Frontend renders: text answer + table/chart + collapsible SQL block
```

**System prompt design principles:**
- GOVERNANCE.md is loaded in full as the system prompt at every turn
- The LLM is explicitly instructed to generate only SELECT statements
- It is instructed to never expose PII columns in query results
- It is instructed to ask exactly one clarifying question per turn if the query
  is ambiguous, rather than assuming
- It is instructed to say clearly when a query is outside the scope of available data

---

### 4.4 SQL Validation and Safety Layer

This layer sits between the LLM output and the database. It is not optional — it is the
primary defence against LLM-generated SQL that could cause data exposure or corruption.

**Checks performed (in order):**

1. **Statement type check:** Parse the SQL and verify the root statement is SELECT.
   Any other statement type (INSERT, UPDATE, DELETE, DROP, CREATE, TRUNCATE, EXPLAIN,
   COPY, or any DDL) is rejected immediately. The query is logged and the user receives
   a failure message.

2. **PII column check:** Scan the SELECT clause for known PII columns
   (`patient_name`, `patient_mobile_number`, `patient_dob`, `date_of_birth`,
   `ben_mobile_no`, `ben_email_id`, `name`, `father_name`, `aadhaar_no`, `abha_id`,
   `ben_ref_id`). If any appear, reject and log.

3. **RBAC filter injection:** Based on the user's role, inject additional WHERE clauses
   before execution. See Section 4.5 for role definitions.

4. **Execution against read-only connection:** The PostgreSQL connection used for
   execution is bound to a role with GRANT SELECT only. Even if the validation layer
   fails to catch a write statement, the database will reject it.

5. **Error handling:** Any execution exception is caught. The full error, the generated
   SQL, the user's original question, and the session context are written to the query
   log. The user receives: "I was unable to answer that question. This has been logged
   for review." No stack trace or SQL is shown to the user on failure.

---

### 4.5 RBAC Architecture

**Roles (prototype):**

| Role | Access |
|---|---|
| `viewer` | National-level and state-level aggregates only. No district-level, no hospital-level, no individual case data. |
| `analyst` | State-level and district-level aggregates. No hospital identifiers, no individual case data. |
| `senior_analyst` | All of the above plus hospital-level data (aggregated). No individual case data. |
| `admin` | Full access to all aggregated data. Can also access query logs and error logs. |

**Enforcement:**
- JWT contains the user's role, issued at login
- The RBAC filter injection step (Section 4.4, step 3) modifies generated SQL before
  execution to enforce role limits
- If the LLM generates a query that requests data beyond the user's role,
  the injected WHERE clause restricts the result rather than blocking the query —
  the user gets a scoped answer with a note that their access is limited to their role level
- Read-only enforcement at the PostgreSQL role level is independent of RBAC and applies
  to all roles equally

---

### 4.6 Semantic / Geography Layer

This layer pre-processes user input before it reaches the LLM, normalising place names
and time references into canonical values.

**Geography resolution:**
- Maintains a reference table of all states and districts with LGD codes, including
  post-2011 splits (Telangana, Ladakh, Palghar, etc.)
- When a user mentions a place name, the system resolves it to the LGD code before
  injecting it into the LLM context
- Ambiguous names (same district name in multiple states) trigger a clarifying question:
  "Did you mean Aurangabad in Maharashtra or Bihar?"
- If a district name returns no results, the system checks for pre-split parent
  district codes and surfaces that to the user
- **Geographic scope differs between the two tables and the semantic layer must know this.**
  The claims table (TMS) excludes brownfield states (Rajasthan, Maharashtra, Karnataka,
  Andhra Pradesh, Tamil Nadu, Telangana, West Bengal) because those states run their own
  SHA trust-model claims systems outside this prototype's scope. The beneficiary table (BIS)
  covers all of India, brownfield states included, since BIS is a registration registry, not
  a claims system. A query like "claims paid in Maharashtra" should return a scoped
  no-data response, not zero, since zero would misleadingly imply no claims occurred.
  A query like "registered beneficiaries in Maharashtra" is valid and answerable.

**Time reference resolution:**
- Resolves natural language time references to explicit date ranges before SQL generation
- "Last year" → derives from current date
- "Q2 2023-24" → `admission_dt >= '2023-10-01' AND admission_dt < '2024-01-01'`
- "Last month" → derives from current date
- "Since the scheme started" → `admission_dt >= '2018-09-23'` (AB PM-JAY launch date)
- Resolved date ranges are injected into the LLM context and shown as chips in the UI
- Prototype TMS data spans FY2025-26 (1 April 2025 to 31 March 2026) only; queries for
  periods outside this range should be flagged as outside the prototype's data window

**HBP specialty synonym mapping:**
- Maps common clinical language to HBP specialty codes
- "cardiac", "heart surgery" → MC (Cardiology), SV (CTVS)
- "cancer", "oncology" → MO, MR, SC
- "delivery", "maternal" → SO (Obstetrics & Gynecology)
- "eye", "cataract" → SE (Ophthalmology)
- **Version caveat:** the procedure/specialty codes and prices used in the synthetic TMS
  data are sourced from NHA's published HBP 2.0 package master, not HBP 2.2. A full HBP 2.2
  code-and-price table has not yet been located and parsed; only the 2.2 user guidelines
  document (rules, not the package list) has been reviewed. Most codes carried forward from
  2.0 to 2.1/2.2 largely unchanged, but some packages were added, restructured, or repriced
  in later versions. This is tracked as an open item in Section 13.

---

## 5. Query Logging

Query logging is a first-class feature, not an afterthought. It serves two purposes:
auditing what the tool is being used for, and continuously improving GOVERNANCE.md by
identifying cases where the LLM generated incorrect or failed SQL.

**What is logged per query:**

| Field | Description |
|---|---|
| `query_id` | UUID |
| `session_id` | Session identifier |
| `user_id` | Anonymised user identifier |
| `user_role` | Role at time of query |
| `timestamp` | Query timestamp |
| `original_question` | Exact text the user sent |
| `resolved_geography` | LGD codes resolved from the question |
| `resolved_period` | Date range resolved from the question |
| `generated_sql` | Full SQL generated by the LLM |
| `execution_status` | `success` or `error` |
| `error_message` | PostgreSQL error if execution failed (null on success) |
| `row_count` | Number of rows returned (null on error) |
| `response_shown` | The answer text shown to the user |

**Log access:** Admin role only, via `/query-log` endpoint.

**Use for GOVERNANCE.md improvement:** Failed queries (execution_status = 'error') are the
primary input for GOVERNANCE.md refinement. Each failure is reviewed, the root cause
identified (missing rule, ambiguous column definition, edge case not covered), and a
fix is encoded as an explicit rule or example in GOVERNANCE.md.

---

## 6. GOVERNANCE.md — The Governance Layer

GOVERNANCE.md is the system prompt loaded into the LLM at every turn. It is the primary
mechanism for ensuring the LLM generates correct, safe, and domain-appropriate SQL.

**GOVERNANCE.md contains:**
- Full table schemas with column names, types, and descriptions
- Known data quality rules and caveats (e.g. pipe-separated procedure codes, m_flag)
- Business definitions (e.g. exactly how claim paid rate is calculated)
- HBP 2.2 domain rules (LAMA/DAMA payment logic, workflow approval chain)
- Geography conventions (LGD codes, post-2011 splits)
- SQL conventions (which columns to use for which calculations)
- PII prohibition rules
- Response format instructions

**GOVERNANCE.md is a living document.** It is updated as the system is used:
- Every SQL execution error triggers a review
- Every analytically incorrect answer (caught by users or analysts) triggers a rule addition
- Version-controlled alongside the codebase
- Changes to GOVERNANCE.md are treated as code changes: reviewed, tested, deployed

The current GOVERNANCE.md for this project is maintained at `backend/GOVERNANCE.md`.

---

## 7. Prototype Data Model

The prototype uses synthetic data built to mirror the real structure of two NHA source
systems: TMS (`claim_paid_excel_t`) and BIS (`t_bis_beneficiary_dtl`). Column names, types,
and geographic reference codes (LGD) were taken directly from the real data dictionaries
and district master supplied by NHA/HAAU, not invented. Values within each column are
synthetic.

### 7.1 Table 1: Treatment / Claims (mirrors TMS)

**78 columns**, matching the real `claim_paid_excel_t` data dictionary exactly. Full schema
is maintained in GOVERNANCE.md.

**Geographic scope:** excludes brownfield states (Rajasthan, Maharashtra, Karnataka, Andhra
Pradesh, Tamil Nadu, Telangana, West Bengal). Covers 548 districts across the remaining
states and union territories. District-level case volume is population-weighted (28-38%
estimated PMJAY-eligible fraction × 1.5-3.5% annual utilization rate), floored at 550 and
capped at 1,200 per district for prototype manageability, with small UTs, north-eastern
states, and Ladakh floored at 500-600 regardless of population. This means district-level
row counts in the prototype are not directly proportional to real population at the high
end — a modelling simplification the query tool should not treat as a real utilization
signal without this caveat.

**Volume:** 586,872 rows, spanning FY2025-26 (1 April 2025 to 31 March 2026).

**Join key:** `member_id`, format `PJ{2-letter state code}{5-character alphanumeric}`
(e.g. `PJGJ5LRGX`). This is the same value that appears as `card_no` in the BIS table —
that is the intended join path between the two tables, not BIS's own `member_id` column,
which is a separate internal identifier.

**Repeat-visit design:** not every row is a unique patient. 493,383 unique patients produce
586,872 rows — roughly 93,500 rows are repeat visits, concentrated in three recurring-care
categories that are never generated as standalone single-visit cases:
- Haemodialysis / peritoneal dialysis (`MG072B`): 5-10 visits per patient, 2-4 day intervals
- Chemotherapy (any `MO`-prefixed code): 5-10 cycles per patient, 14-28 day intervals
- ECT / TMS sessions (`MM009A`, `MM010A`): 5-10 sessions per patient, 2-4 day intervals

Repeat-visit patients share the same `member_id`, demographics, and — roughly 90% of the
time — the same `hospital_code` across their visits, reflecting continuity of care. A query
counting "patients" must use `COUNT(DISTINCT member_id)`, not `COUNT(*)`, or it will
overcount anyone in recurring care. This distinction should be an explicit GOVERNANCE.md rule.

**Hospitals:** a separate hospital master of 5,729 hospitals, roughly 10-11 per district,
sampled from repeatedly rather than one hospital per row. Hospital codes follow
`HOSP{2-digit state}{G|P}{5-digit}`, where `G`/`P` denotes government or private
(e­.g. `HOSP23P64469`). Roughly 21% of cases are treated outside the patient's home
district, routed via real state-border adjacency rather than uniformly at random.

**Procedure/specialty codes:** sourced from NHA's published HBP 2.0 package master
(~230 curated codes across 23 specialties), with amounts derived from real base package
prices with ±10-25% variance. See the HBP 2.2 caveat in Section 4.6.

**Workflow / financial columns:** admission through CPD → ACO → SHA approval chain,
TAT columns (`preauth_tat`, `claim_tat`, `payment_tat`) computed in hours (a prototype
assumption — real units are still an open question, see Section 13). Case outcome mix is
85% paid / 8% pending / 7% rejected for single-visit cases; recurring-care visits get a
higher paid rate (92/5/3) reflecting an established patient-provider relationship.

### 7.2 Table 2: Beneficiary Information System (mirrors BIS)

**63 columns**, matching the real `t_bis_beneficiary_dtl` sample file. Note this is wider
than the official data dictionary, which documents only 46 columns — 19 columns present in
the real sample file have no dictionary entry (`family_id`, `member_id`, `bis_family_id`,
`bis_member_id`, `house_no`, `pincode`, `address`, `src_flag`, `aadhaar_no`, `gender`,
`year_of_birth`, `name`, `father_name`, `age`, `primary_ben_id`, `match_score`,
`source_type`, `aadhaar_disp_code`, `yob_secc`). Values for these were generated on
reasonable assumptions documented inline in GOVERNANCE.md; several are flagged for NHA/HAAU
confirmation in Section 13.

**Geographic scope:** all of India, 786 districts, including the seven brownfield states
that TMS excludes. This is a deliberate asymmetry (Section 4.6) — BIS is a registration
registry independent of which claims system a state uses.

**Volume:** 1,169,814 rows.

**Join key:** `card_no`, format `PJ{2-letter state code}{5-character alphanumeric}`. Every
one of TMS's 493,383 unique patients has exactly one BIS row where `card_no` equals their
TMS `member_id` — verified 1:1, no duplicates, no gaps. The remaining ~676,000 rows are
beneficiaries with no TMS claim: household members of TMS patients, and the entire
brownfield-state population (no TMS presence at all in this prototype).

**Sizing logic:** registered beneficiaries were set at 1.5× the number of unique TMS
patients (or an equivalent population-derived estimate for brownfield districts, where no
TMS data exists to anchor against), not at the full estimated PMJAY-eligible population.
Modelling the true eligible population at 80-85% registration would have produced roughly
380 million rows nationally — unusable for a prototype. This means BIS in its current form
is a small proportional sample of registration volume, not a population-scale registry;
if HAAU needs to test at population scale later, this ratio is the lever to change.

**Household structure:** because the 1.5× ratio leaves little row budget per TMS patient,
most households in this synthetic BIS are solo registrations or couples, occasionally with
one child, rather than full 4-5 person families. Relation codes (`REL01`-`REL09`, see 7.4)
follow the source file's own coding, including its gaps.

### 7.3 Reference Tables (seed data, not synthetic)

```sql
CREATE TABLE lgd_states (
  lgd_code    SMALLINT PRIMARY KEY,
  state_name  VARCHAR(100),
  region      VARCHAR(50)
);

CREATE TABLE lgd_districts (
  lgd_code      SMALLINT PRIMARY KEY,
  district_name VARCHAR(100),
  state_lgd_code SMALLINT REFERENCES lgd_states(lgd_code),
  split_year    INTEGER   -- year of post-reorganisation split, if applicable
);

CREATE TABLE hbp_specialties (
  specialty_code  VARCHAR(4) PRIMARY KEY,
  specialty_name  VARCHAR(100),
  case_type       VARCHAR(10)  -- MEDICAL, SURGICAL, or BOTH
);
```

**Hospital Empanelment Module (HEM):** Deferred. Will be added as a third table
when the schema is confirmed. The architecture accommodates it without structural change.

### 7.4 Known Data Quality Quirks (Deliberately Preserved)

The real source files supplied by NHA/HAAU contain quirks that were preserved rather than
cleaned, because a production-facing query tool needs to handle the data as it actually
exists, not an idealised version of it. These should each become an explicit GOVERNANCE.md rule
so the LLM does not silently "correct" them into wrong SQL:

| Quirk | Where | Detail |
|---|---|---|
| State name misspelling | District master → both tables | Maharashtra is spelled `MAHARASTRA` (missing the H) in the source LGD file. Both TMS and BIS use this spelling exactly. |
| Duplicate-cased state name | District master → BIS only | Rajasthan appears under the same `state_cd` with two different castings (`RAJASTHAN` and `Rajasthan`) in different source rows. Both castings are preserved in brownfield-state BIS rows. |
| Shared LGD code | District master → BIS only | Dadra and Nagar Haveli and Daman and Diu share a single `state_cd` (post-merger administrative reality), appearing as two distinct `state_name` values under one code. |
| Missing relation code | BIS `relation` column | `REL07` does not appear anywhere in the source relation code list; the sequence jumps from `REL06` to `REL08`. |
| Duplicate relation code | BIS `relation` column | `REL09` is documented against two different labels ("NOT AVAILABLE" and "OTHERS") in the source file. Both meanings are treated as the same code in the synthetic data. |
| Duplicate-looking status columns | BIS | `enrl_status` and `enrol_status` are two separate columns in the real sample file (different spelling, not a typo to fix), carrying different value sets (short codes vs. full words respectively). |
| Unverified code list | BIS `aadhaar_disp_code` | Populated with a best-effort reconstruction of PMJAY's Aadhaar-exception codes, not verified against an official published list. Flagged for NHA/HAAU confirmation. |

---

## 8. Dialogue Design

### 8.1 Clarifying dialogue patterns

The system asks exactly one clarifying question per turn. It does not ask multiple
questions at once. It does not assume values for ambiguous inputs.

**Missing geography:**
> User: "How many claims were paid last quarter?"
> System: "Which state are you looking at, or would you like national-level figures?"

**Ambiguous district:**
> User: "Show me claims in Aurangabad"
> System: "There are two districts named Aurangabad — one in Maharashtra and one in Bihar. Which did you mean?"

**Out-of-scope query:**
> User: "What is the budget allocation for PM-JAY next year?"
> System: "Budget allocations aren't in my current dataset. I can show you actual amounts claimed and paid for past periods if that's useful."

**SQL execution failure:**
> User: "Show me claims by sub-district for Rajasthan"
> System: "I wasn't able to answer that question — the query I generated didn't execute successfully. This has been logged for review. You could try rephrasing, or ask for district-level data which I can answer reliably."

### 8.2 Context accumulation across turns

Session context tracks confirmed geography and period across turns.
Once confirmed, values persist until the user explicitly changes them.

```json
{
  "session_id": "abc123",
  "confirmed_context": {
    "state_lgd_code": 1,
    "state_name": "Jammu & Kashmir",
    "period_start": "2023-10-01",
    "period_end": "2024-01-01",
    "period_label": "Q2 2023-24"
  },
  "conversation_history": [...]
}
```

---

## 9. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React (TypeScript) | Vite build; Tailwind for styling |
| Backend | FastAPI (Python 3.11+) | Async; Pydantic for validation |
| Database | PostgreSQL 15 | Read-only application role |
| LLM | Llama 3 8B via Ollama (prototype) | Swappable; OpenAI-compatible interface abstracted behind a single client class |
| Auth | JWT (python-jose) | Short-lived tokens |
| SQL parsing | sqlglot or sqlparse | For statement type validation before execution |
| Containerisation | Docker + Docker Compose | Single-command local dev startup |
| Secrets management | `.env` file (prototype); Vault / K8s secrets (production) | |

---

## 10. Security Constraints

- LLM system prompt explicitly prohibits generating any non-SELECT SQL statement
- SQL validation layer rejects any non-SELECT statement before execution regardless
  of system prompt compliance
- PostgreSQL read-only role enforces SELECT-only at the database level as a third
  independent control
- PII columns are explicitly listed in GOVERNANCE.md and in the SQL validation layer;
  any query selecting them is rejected
- JWT required on all endpoints except `/auth/login`
- CORS restricted to known frontend origin
- Rate limiting on `/chat/message` (prototype: 60 requests/minute per user)
- No real PII in the prototype database — synthetic data only
- Query log access restricted to admin role

---

## 11. Directory Structure

```
nha-copilot/
├── backend/
│   ├── GOVERNANCE.md                    # LLM system prompt — governance layer
│   ├── app/
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── auth/                    # JWT logic, RBAC
│   │   ├── chat/                    # Message handling, session context
│   │   ├── nl_to_sql/               # LLM client, SQL generation pipeline
│   │   │   ├── client.py            # Abstracted LLM client (swap model here)
│   │   │   ├── pipeline.py          # Full generation → validation → execution pipeline
│   │   │   └── prompt_builder.py    # Injects GOVERNANCE.md + session context into prompt
│   │   ├── sql_safety/              # SQL validation and safety layer
│   │   │   ├── validator.py         # Statement type check, PII check
│   │   │   └── rbac_filter.py       # Role-based WHERE clause injection
│   │   ├── semantic/                # Geography and time resolution
│   │   │   ├── geography.py         # LGD code resolution
│   │   │   ├── time_resolver.py     # Natural language → date range
│   │   │   └── synonyms.py          # HBP specialty synonym mapping
│   │   ├── query_log/               # Query logging
│   │   │   └── logger.py
│   │   └── db/                      # DB connection, read-only session
│   ├── seed_data/                   # Synthetic data generation scripts
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── SqlViewer.tsx        # Collapsible SQL block
│   │   │   ├── ContextChips.tsx     # Active geography/period chips
│   │   │   └── ResultTable.tsx
│   │   ├── hooks/
│   │   └── App.tsx
│   └── package.json
├── docker-compose.yml
└── README.md
```

---

## 12. Prototype Scope Boundary

The following are explicitly out of scope for the prototype:

- Push/digest layer (Pulse) — deferred to Phase 2
- Anomaly detection and data quality monitoring (Sentinel) — deferred to Phase 3
- Hospital Empanelment Module (HEM) — deferred within prototype; architecture accommodates it
- File export (PDF/Excel of query results)
- Cross-session memory
- Real PM-JAY or ABDM data — prototype uses synthetic data only
- Integration with live NHA APIs or data pipelines
- Multi-user simultaneous session testing

---

## 13. Open Questions / Decisions Pending

| # | Question | Owner | Priority |
|---|---|---|---|
| 1 | Which LLM endpoint for prototype — local Ollama or Indian cloud provider API? | Developer | High |
| 2 | Which RBAC roles map to which actual NHA/SHA designations? | CHAI / NHA | Medium |
| 3 | What is `service_request_type = 'C'` in the TMS data? Still unresolved; synthetic data includes it as a small residual category (~2%) without semantic meaning assigned. | CHAI / NHA | Medium |
| 4 | What are the real units of `preauth_tat`, `claim_tat`, `payment_tat`? Prototype assumes hours; unconfirmed against production. | CHAI / NHA | Medium |
| 5 | Are there additional geography edge cases beyond post-2011 district splits? | CHAI | Low |
| 6 | Is there a full HBP 2.2 code-and-price master available, or is 2.0-based realism acceptable for the prototype's purposes? Confirmed acceptable for now (see Section 4.6); revisit if production alignment is needed. | CHAI / NHA | Low |
| 7 | What are the real, published Aadhaar-exception codes for `aadhaar_disp_code` in BIS? Current values are a best-effort reconstruction. | CHAI / NHA | Medium |
| 8 | Confirm the intended semantics of the 19 undocumented BIS columns (Section 7.2) against the real production table, particularly the relationship between `family_id`/`member_id` (assumed central PMJAY-wide IDs) and `bis_family_id`/`bis_member_id` (assumed BIS-internal IDs). | CHAI / NHA | Medium |
| 9 | If population-scale beneficiary volume is needed later (versus the current 1.5×-of-TMS-patients prototype sample), confirm the target registration-to-eligible ratio so the sizing logic can be re-run at the intended scale. | CHAI / NHA | Low |

---

*End of document.*
