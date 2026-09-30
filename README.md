# NHA Analytics Co-Pilot (Prototype)

[![Live App](https://img.shields.io/badge/%F0%9F%9A%80%20Live%20App-Open-0f7c8b?style=for-the-badge)](https://zerobug-mohit.github.io/nha-copilot/)

> **Live demo:** https://zerobug-mohit.github.io/nha-copilot/

A web-based **natural-language → SQL** chat co-pilot for NHA / ABDM officials to
query **ABDM (Ayushman Bharat Digital Mission) rollout data** in plain English —
health facility (HFR) and professional (HPR) registration, ABHA (health ID)
creation, health-record linking, and digital-transaction adoption (Scan & Share,
Scan & Pay) across facilities, bridges, states and districts. Data layer is
**Google BigQuery**; the LLM is **OpenAI**.

```
Browser (React) ──HTTPS──▶ FastAPI backend ──read-only SQL──▶ BigQuery
                              │  auth / RBAC
                              │  NL-to-SQL (GOVERNANCE.md + OpenAI)
                              │  SQL safety (SELECT-only, PII, sqlglot)
                              │  semantic (LGD geography, time)
                              └  query log (SQLite)
```

## Safety model (three independent layers)
1. **System prompt** (`backend/GOVERNANCE.md`) — instructs the LLM to emit only a single `SELECT`.
2. **SQL validation** (`sql_safety/validator.py`) — parses with sqlglot, rejects
   any non-SELECT / multi-statement / PII-column query before execution.
3. **IAM read-only** — the BigQuery service account is granted only
   `bigquery.dataViewer` + `bigquery.jobUser`, so the database itself refuses writes.

Facility identity (name / ID / address) is public dashboard data and is
displayable; the only hard-blocked PII is `abha_address` (a patient ABHA address,
already removed at data prep). RBAC (`viewer` / `analyst` / `senior_analyst` /
`admin`) restricts query *granularity* (state → district → facility) via column
inspection.

## Data model — 9 ABDM tables (no merged table)
The co-pilot queries nine tables directly, joined by **facility ID** and/or
**geography**. There is no pre-built merged table. The authoritative schema,
join rules, and business rules live in **`backend/GOVERNANCE.md`** (the governance
prompt). In brief:

| Table | Use it for |
|---|---|
| `health_facility_registry` | facility profile: type, ownership, verification, address |
| `health_professionals_registry` | HPR (doctor/nurse/pharmacist) registration counts |
| `healthid_top_indicators` | ABHA (health ID) creation counts |
| `healthid_linked_trend` | health-record linking activity (by document type) |
| `linked_facility` | facility ↔ bridge links and whether active |
| `scan_and_share` | Scan & Share transaction counts |
| `scan_pay_count` | Scan & Pay transaction counts and payment amounts/status |
| `state_district_master` | full-India code ↔ name lookup (join carefully — see below) |
| `integrator_detail` | bridge / software-vendor reference (status, milestone, ownership) |

**Key rules encoded in `GOVERNANCE.md`** (verified against the data):
- Facilities → `COUNT(DISTINCT hfr_id)`. ABHA created → `SUM(overall_count)`
  (`today_count` is ~always 0). HPR → `SUM(registered_count)`. Records linked →
  `SUM(record_linked_count)` (never `hid_linked_count`). Scan & Share →
  `SUM(counts)`; Scan & Pay → `SUM(facility_count)` / `SUM(payment_amount)`.
- **Coverage:** the activity tables currently hold only **Bihar (10)** and
  **Andhra Pradesh (28)** — the model answers honestly for any other state.
- **Coded values:** `facility_ownership` is `G`/`P`/`PP` (not spelled out);
  `active` is `t`/`f`; `hpr_type` is `d`/`n`/`p`. (`ownership` /
  `facility_ownership_desc` are full text.)
- **Join fan-out:** `state_district_master` is one row per district — to name a
  state-level aggregate, join a `SELECT DISTINCT state_code, state_name` subquery,
  never the raw master on `state_code` alone.
- **Dates:** mostly `DATE`, but `healthid_linked_trend.created_date` and
  `scan_and_share.date_created` are `DATETIME` (wrap with `DATE(col)` for day-level filters).
- Join keys: `hfr_id` = `hip_id`; `hosp_id`/`service_id` may carry a `_N` suffix
  (strip before joining); `bridge_id` = `integrator_detail.production_bridge_id`.

## Prerequisites
- Python 3.11+ and Node 18+ (verified on Python 3.13 / Node 24).
- A BigQuery dataset holding the nine tables above and a service-account JSON key
  with read-only access. Load the CSVs with an **explicit schema** (all ID/code
  columns as STRING) — do not use CSV autodetect.
- An OpenAI API key.

## Setup

### Backend
```bash
cd backend
cp .env.example .env          # fill in GCP project/dataset, key, OpenAI key, JWT secret
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# smoke-test BigQuery connectivity + read-only enforcement:
./.venv/Scripts/python.exe scripts/smoke_bq.py
# run the API:
./run.ps1                     # or: uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (proxies /auth, /chat to :8000)
```
For a **production build** (self-hosting), copy `.env.example` to `.env`, set
`VITE_API_BASE` (backend URL) and `VITE_BASE` (serving path), then `npm run build`
→ output in `frontend/dist/`. Full guide: [`deploy/SELF_HOSTING.md`](deploy/SELF_HOSTING.md).

## Prototype users
`viewer` / `analyst` / `senior` / `admin`, password = `<username>123`
(e.g. `analyst` / `analyst123`). Override in production via the `APP_USERS` env var.

## Endpoints
| Endpoint | Method | Notes |
|---|---|---|
| `/auth/login` | POST | issues JWT |
| `/chat/message` | POST | main chat turn (rate-limited 60/min) |
| `/chat/session/{id}` | GET | session history |
| `/report/weekly` | GET | weekly ABDM report payload |
| `/explorer` | GET | proactive insight cards |
| `/query-log` | GET | admin only |
| `/health` | GET | liveness |

## Tests
```bash
# Test deps are in requirements-dev.txt (which includes requirements.txt):
cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
ALLOW_INSECURE_DEV=1 ./.venv/Scripts/python.exe -m pytest -q   # 140 tests
cd ../frontend && npm test                                     # chart-decision engine
```
Backend coverage: SQL safety (incl. `SELECT *` rejection), RBAC, semantic layer,
pipeline, session store, query log, repeated-question cache, auth/password
hashing, weekly report, Explorer, PDF chat, and HTTP-level auth/compression.

`ALLOW_INSECURE_DEV=1` is needed because the app refuses to start with the
default `JWT_SECRET` and seed accounts — see [`issues.md`](issues.md) #4.
A live eval harness (hits OpenAI + BigQuery) covers routing, coded values,
joins, dates, geography, language mirroring, and numeric accuracy vs BigQuery:
```bash
cd backend && ./.venv/Scripts/python.exe scripts/eval_model.py
```

## Deployment
Frontend builds to static files; backend runs as a service behind a reverse
proxy. Set `OPENAI_MODEL`, `APP_USERS`, and the GCP credentials via the
environment; table names default to the loaded names in `app/config.py` and only
need overriding if yours differ.

- **Self-hosting on your own infrastructure (nginx / IIS / Docker):** see
  **[`deploy/SELF_HOSTING.md`](deploy/SELF_HOSTING.md)** — a step-by-step,
  clone-and-build guide. The frontend's two build settings (`VITE_API_BASE` and
  `VITE_BASE`) come from `frontend/.env` (copy `frontend/.env.example`).
- **Reference GCP VM + GitHub Pages setup:** see [`deploy/README.md`](deploy/README.md).
