# Running this tool for PM-JAY as well as ABDM

> **Status:** plan + in-progress implementation.
> **Audience:** whoever maintains or deploys this tool.

## The short version

This tool already *was* a PM-JAY tool. It was migrated to ABDM in commit
`532baa1` ("Migrate domain: PM-JAY claims/BIS -> ABDM digital-adoption"). Every
piece of the PM-JAY domain layer is still in git history and has been recovered.

So this is **not** a port to a new domain. It is:

1. Separating the *domain* (which scheme, which tables, which rules) from the
   *platform* (auth, safety, charts, exports, RAG) — because right now they are
   tangled across about twenty files.
2. Rebuilding the PM-JAY domain from history, **on top of the current hardened
   platform** rather than the old code it shipped with.

Point 2 is the important one. See "Why not just check out the old files".

---

## What is recoverable from history

All of this came back intact from `532baa1^`:

| Recovered | Size | Notes |
|---|---|---|
| `backend/CLAUDE.md` | 26.6 KB | The full PM-JAY governance doc — 9 sections, both table schemas, domain rules, data-quality quirks, HBP section, output format |
| `app/semantic/synonyms.py` | 4.9 KB | HBP specialty mapping: 25 specialties, 68 synonym phrases, whole-word matching. **Complete** |
| `app/report/service.py` | 12.0 KB | PM-JAY weekly report aggregates + its LLM summary prompt |
| `app/sql_safety/validator.py` | 4.2 KB | **The real PII list — 16 columns** |
| `app/sql_safety/rbac_filter.py` | 4.0 KB | `HOSPITAL_COLUMNS` (incl. bank details) + `DISTRICT_COLUMNS` tiers |
| `app/config.py` | 3.3 KB | Table names: `TMS_Sample`, `BIS_Updated_Sample`, `BIS_TMS_Sample_Merged` |
| `app/explorer/service.py` | 4.2 KB | PM-JAY explorer idea-generation prompt |
| `scripts/create_merged_table.sql` | 5.4 KB | The BIS ⟕ TMS merged-table definition |
| `scripts/eval_model.py` | 14.2 KB | The PM-JAY eval harness and its question set |
| `reference/HBP-2022.pdf` | — | HBP package master, still referenced |

Plus `docs/history/architecture-pmjay-superseded.md`, which is already in the
working tree and documents the data model, volumes, join keys and the
deliberately-preserved source-data quirks.

## Why not just check out the old files

Because the old PM-JAY code predates every fix made to this repo. Two concrete
examples found while reading it back:

- **Its validator has no `SELECT *` check.** That is the single worst bug found
  in the ABDM review (`issues.md` #1): a star projection defeats *both* the PII
  scan and the RBAC tier check, because both work by enumerating the
  `exp.Column` nodes a query references and `SELECT *` references none. On ABDM
  that leaked facility names to a `viewer`. **On PM-JAY the same hole leaks
  `patient_name`, `aadhaar_no` and `ben_mobile_no`** — the old code's own PII
  list, bypassed. It also has no `MAX_ROWS` / `enforce_row_limit`.
- **Its RBAC fails open.** `check_rbac` returns `allowed=True` when sqlglot
  cannot parse the SQL. The current version fails *closed*, deliberately, with a
  comment explaining that it must not depend on the validator having run (the
  two use `parse()` vs `parse_one()` and can legitimately disagree). The old
  version also has the `blocked_columns: list[str] = None` mutable-default bug.

The same applies to the 43 other fixes and 18 optimizations: cookie auth,
bcrypt 72-byte truncation, the district-collision fix affecting 55 names, the
`"q"`-anywhere year bug, SQLite-backed sessions, the concurrent report, gzip.

**Therefore: port the old domain *content* onto the current *mechanism*.** Never
copy an old module wholesale.

---

## Architecture: domain packs

One repository, one platform, two domain packs, selected by config.

```
backend/app/domains/
  base.py              DomainPack — the contract
  __init__.py          get_domain(), table_ref(), placeholder derivation
  abdm/
    __init__.py        the ABDM pack
    GOVERNANCE.md      (moved from backend/GOVERNANCE.md)
    report.py          ABDM weekly aggregates
  pmjay/
    __init__.py        the PM-JAY pack
    GOVERNANCE.md      (recovered + updated)
    report.py          PM-JAY weekly aggregates
    synonyms.py        HBP specialty mapping (PM-JAY only)
```

A pack declares everything that is scheme-specific:

- `tables` — logical key to default BigQuery table name
- `table_labels` — headings for the live-schema block
- `pii_columns` — hard backstop, applies to **every** role including admin
- `facility_tier_columns` / `district_tier_columns` — RBAC granularity tiers
- `data_window` + note — the "is this ask outside our data" guard
- `governance_file` — the system prompt
- `explorer_system`, `report_system` — the two domain LLM prompts
- `build_report` — the weekly aggregate builder
- `ui` — frontend copy and coded-value maps

### Placeholder derivation kills a bug class

Today a table must be registered in **three** places that can silently drift:
`config.table_map`, `prompt_builder._PLACEHOLDERS`, `schema._TABLE_LABELS`.

Placeholders follow a strict convention in both domains —
`facility_registry` to `{FACILITY_REGISTRY_TABLE}`, `tms` to `{TMS_TABLE}` —
verified against all 9 ABDM and all 3 PM-JAY placeholders. So they are now
**derived** from the table key (`f"{{{key.upper()}_TABLE}}"`) and
`_PLACEHOLDERS` is deleted. Two sync points instead of three, and the remaining
one is a single dict in the pack.

### Table-name overrides stay backward compatible

`table_ref(key)` resolves `getattr(cfg, f"bq_{key}_table", None) or pack.tables[key]`.
Existing env vars (`BQ_FACILITY_REGISTRY_TABLE`, ...) keep working untouched;
PM-JAY adds `BQ_TMS_TABLE`, `BQ_BIS_TABLE`, `BQ_MERGED_TABLE` by the same
convention. Everything is read through `Settings`, never `os.getenv` — that is
exactly the bug fixed in `a7be906`, where `ALLOW_INSECURE_DEV` in `backend/.env`
was silently ignored.

---

## Domain changes that are not find-and-replace

Three things genuinely differ in kind, not just in naming.

### 1. PII becomes real

ABDM is public dashboard data. `GOVERNANCE.md` §0.2 says so outright: *"Facility
identity is NOT sensitive in this dataset."* `PII_COLUMNS` is a single entry,
`abha_address`, kept only as a backstop.

**PM-JAY is patient-level.** The recovered list is 16 columns:
`patient_name`, `patient_dob`, `patient_mobile_number` (and their `tms_`-prefixed
merged-table forms), `name`, `father_name`, `aadhaar_no`, `abha_id`,
`ben_mobile_no`, `ben_email_id`, `ben_ref_id`, `date_of_birth`,
`obj_aadhar_vault`.

The good news: `validate_sql` runs **before and independently of role**
(`pipeline.py:258`), so admin does not bypass it. The mechanism is already
right; only the list was near-empty.

Additions beyond the recovered list, from the superseded architecture doc §7.2:
`year_of_birth`, `yob_secc`, `house_no`, `address`, `pincode`, `match_score`,
`primary_ben_id`, `aadhaar_disp_code`.

### 2. RBAC needs a tier it does not have

The old PM-JAY `HOSPITAL_COLUMNS` mixes two different things: hospital identity
(`hospital_code`, `hospital_name`) and **bank details** (`hosp_pan_number`,
`hosp_account_number`, `src_ifsc_code`, `ben_ifsc_code`). Bank details are not a
granularity tier — no analyst tier should see them. They move to `pii_columns`.

Its `DISTRICT_COLUMNS` likewise mixes district geography (`dist_cd`, `dist_name`)
with beneficiary street address (`house_no`, `address`, `pincode`). Those are
individual identification, not district granularity. They move to `pii_columns`.

PM-JAY also needs a **row-level** check that ABDM never needed: a non-aggregated
`SELECT` against the claims table returns individual case rows. That must be
blocked below `senior_analyst` regardless of which columns are named.

### 3. Brownfield-state asymmetry

PM-JAY's claims table (TMS) **excludes seven trust-model states** — Maharashtra,
Karnataka, Rajasthan, Andhra Pradesh, Tamil Nadu, Telangana, West Bengal —
because those states run their own SHA claims systems. The beneficiary table
(BIS) covers all of India.

So "claims paid in Maharashtra" must return a **scoped no-data answer**. Zero
would falsely imply no claims occurred. "Registered beneficiaries in
Maharashtra" is valid and answerable.

There is no ABDM equivalent. ABDM's rule is the opposite, and emphatic:
*"coverage is national — all ~36 states/UTs are present ... do NOT restrict to
any subset of states."* This asymmetry is a per-pack semantic rule, in the
governance doc and as a pre-flight check.

### Also carry over, or the model will "fix" them into wrong SQL

From the superseded doc §7.4, quirks deliberately preserved from the real source
files:

- Maharashtra is spelled `MAHARASTRA` (no H) in the source LGD file, and both
  tables use that spelling exactly.
- Rajasthan appears as both `RAJASTHAN` and `Rajasthan` under one `state_cd`.
- Dadra & Nagar Haveli and Daman & Diu share a single `state_cd`.
- `REL07` does not exist; the relation sequence jumps `REL06` to `REL08`.
- `enrl_status` and `enrol_status` are two separate real columns with different
  value sets — not a typo to collapse.

And the counting rule: **`COUNT(DISTINCT member_id)`, never `COUNT(*)`.** About
93,500 of 586,872 claim rows are repeat visits (dialysis, chemotherapy, ECT), so
`COUNT(*)` overcounts anyone in recurring care.

---

## Frontend

The frontend learns the active domain at **runtime** from a new public
`GET /meta`, so one build serves both deployments. `frontend/src/domain.ts`
holds per-domain copy and coded-value maps; `chartEngine.COLUMN_MAPS`,
`ChatWindow` sample questions and banners, `Login` tagline, the three export
footers and `WeeklyReport` error copy all read from it.

Note the existing duplication: `lib/reportPptx.ts` carries its own
`ownerName`/`hprName` maps because the weekly deck is built without the chart
engine. Both copies move into `domain.ts` so they cannot drift.

---

## Stages

| Stage | What | Risk |
|---|---|---|
| 1 | `DomainPack` + registry; move ABDM content into `domains/abdm/`, **zero behaviour change**, all 202 backend + 98 frontend tests still green | Highest — it is a wide refactor |
| 2 | Build `domains/pmjay/` from recovered history, onto current mechanisms | Low |
| 3 | Wire consumers: prompt_builder, db/schema, validator, rbac_filter, report, explorer, time_resolver | Medium |
| 4 | `GET /meta` + `frontend/src/domain.ts`; de-duplicate the PPT maps | Low |
| 5 | Tests per domain; row-level RBAC check; brownfield guard; docs + deploy | Low |

Stage 1 is deliberately behaviour-preserving so that if ABDM breaks, it broke in
a refactor with a green test suite to bisect against — not tangled up with new
PM-JAY content.

---

## Open items

Two things need confirming, neither of which blocks the work:

1. **Do the PM-JAY BigQuery tables still exist?** The recovered names are
   `TMS_Sample`, `BIS_Updated_Sample` and `BIS_TMS_Sample_Merged` in
   `nha-conversational-analytics.nha_conversational_analytics`. If that dataset
   was repurposed for ABDM they may be gone, in which case the merged table can
   be rebuilt from `scripts/create_merged_table.sql` — assuming the two source
   tables survive. Table names are config, so the code does not care; this only
   decides whether the PM-JAY deployment can actually run. It cannot be checked
   from here: there are no BigQuery credentials in this environment.
2. **Is PM-JAY data synthetic or real?** The superseded doc describes a
   synthetic set (586,872 TMS rows over FY2025-26; 1,169,814 BIS rows). If real
   production data is loaded instead, then: the data window changes, the PII
   controls stop being theoretical, `bq_max_bytes_billed` (2 GB) and
   `MAX_ROWS` (5000) need review against real volume, and the
   "prototype / verify before use" footers should be re-worded.

Assumed until told otherwise: the recovered table names, and synthetic data.
