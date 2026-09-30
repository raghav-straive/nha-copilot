# NHA Analytics Co-Pilot — Project Brief

> A plain-language description of what this project does and how it works, followed
> by the issues found in it and the optimizations available. No code — each item
> says what is wrong, what it causes, and what to do about it.
>
> **Standalone breakdowns:** [`issues.md`](issues.md) — every issue with its fix
> status · [`optimizations.md`](optimizations.md) — speed and cost improvements.
>
> Companion docs: [`README.md`](README.md) (setup/usage),
> [`backend/GOVERNANCE.md`](backend/GOVERNANCE.md) (authoritative data rules),
> [`docs/README.md`](docs/README.md) (documentation map).
>
> **Note:** Parts 2 and 3 below describe the issues and optimizations *as found*.
> Most have since been fixed in this repository — `issues.md` and
> `optimizations.md` carry the current status for each.

---

# Part 1 — What the project does

## 1. What it is

A chat app that lets Indian government health officials (NHA / ABDM) ask questions
about **ABDM rollout data in plain English, Hindi, or Hinglish** and get back real
numbers, a chart, and the SQL that produced them.

The problem it solves: these officials have deep domain knowledge but can't write
SQL, and there aren't enough analysts to build a dashboard for every question. So
instead of waiting, they just ask.

**The data** — nine tables in Google BigQuery:

| Table | Answers questions about |
|---|---|
| `health_facility_registry` | facility profile: type, ownership, verification, address |
| `health_professionals_registry` | HPR registrations (doctor / nurse / pharmacist) |
| `healthid_top_indicators` | ABHA (health ID) creation counts |
| `healthid_linked_trend` | health-record linking, by document type |
| `linked_facility` | facility ↔ bridge links, and whether active |
| `scan_and_share` | Scan & Share transaction counts |
| `scan_pay_count` | Scan & Pay counts, amounts, payment status |
| `state_district_master` | full-India LGD code ↔ name lookup |
| `integrator_detail` | bridge / software-vendor reference data |

There is no pre-built merged table — the model joins these directly on facility ID
and geography.

## 2. What a user can actually do

| Feature | What it does |
|---|---|
| **Chat with Data** | Ask anything → it writes SQL, runs it, answers with a table + chart + the SQL on display |
| **Data Explorer** | You ask nothing. The AI proposes non-obvious questions itself, runs them, returns insight cards |
| **Chat with PDFs** | Ask about a folder of ABDM policy PDFs; answers cite `[1]`, and clicking a citation opens that PDF **at the exact page with the exact line highlighted** |
| **Weekly Report** | One click → fixed weekly metrics with week-over-week change and an AI executive summary; exports to Excel / PowerPoint |

Supporting touches: voice input (mic, auto-picks `hi-IN` vs `en-IN` from browser
locale), message edit-and-resend, and chart drill-down (click a bar → it composes
the follow-up question for you).

## 3. How the main chat works

`backend/app/nl_to_sql/pipeline.py:93` (`run_turn`) is the heart of it. One question
goes through seven stages:

```
Question
   │
   ├─1─ Resolve geography + time DETERMINISTICALLY (before the LLM sees it)
   │      "Bihar" → LGD state_code 10 ; "last quarter" → a real date range
   │
   ├─2─ Genuinely ambiguous? → stop, ask ONE question with clickable options
   │      (two districts named Aurangabad → don't guess)
   │
   ├─3─ LLM writes SQL   [system prompt = GOVERNANCE.md + live BQ schema]
   │      returns JSON {action, sql, answer_template, chart}
   │      action ∈ sql | clarify | chat | out_of_scope
   │
   ├─4─ VALIDATE: sqlglot parse — single read-only SELECT, no PII column
   │
   ├─5─ RBAC: does the caller's role permit this granularity?
   │
   ├─6─ EXECUTE on BigQuery (read-only account, 2 GB billing cap)
   │      on error → ONE corrective retry with the real error message fed back
   │
   └─7─ SECOND LLM pass over the ACTUAL rows → summary + 2–4 insights
          chart spec validated against the real result set, dropped if it doesn't hold
```

### The real secret sauce: `GOVERNANCE.md`

A ~30 KB hand-written rulebook loaded as the system prompt every turn. It holds the
schema plus every hard-won gotcha, for example:

- count facilities with `COUNT(DISTINCT hfr_id)`, never `COUNT(*)`
- `facility_ownership` is coded `G` / `P` / `PP`, not spelled out; `active` is `t`/`f`; `hpr_type` is `d`/`n`/`p`
- ABHA created = `SUM(overall_count)` (`today_count` is ~always 0)
- records linked = `SUM(record_linked_count)`, **never** `hid_linked_count`
- two date columns are `DATETIME` not `DATE` → wrap in `DATE(col)` for day filters
- joining `state_district_master` on `state_code` alone causes row fan-out → use a `SELECT DISTINCT` subquery
- activity tables currently hold **only Bihar (10) and Andhra Pradesh (28)** → answer honestly for other states

The live BigQuery column types are also fetched at startup and appended, so the
model can't invent a column name. The last ~4 Q&A turns go in too, so *"what about
Andhra Pradesh?"* keeps the previous metric.

**Quality is directly proportional to the quality of this file.** The query log is
the feedback loop for improving it.

### Three independent safety layers

Each is meant to work even if the other two fail:

1. **The prompt** instructs the LLM to emit only a single `SELECT`
2. **The validator** parses the SQL and rejects anything else *before* execution — it does not trust the LLM at all
3. **IAM** — the BigQuery service account holds only read permissions, so the database itself refuses writes

Facility identity (name / ID / address) is public dashboard data and is displayable.
The only hard-blocked PII is `abha_address`, already removed at data prep; the
validator is a backstop in case a refresh reintroduces it.

*(Issue 2 below is a real gap in layer 2 — worth reading.)*

### Roles — granularity, not blocking

| Role | Sees |
|---|---|
| `viewer` | national + state aggregates |
| `analyst` | + district level |
| `senior_analyst` | + individual-facility detail |
| `admin` | everything, plus the query log |

Enforced by inspecting which columns the generated query mentions. Above your tier →
a polite *"try asking at state level instead"*, not a raw error.

### Audit trail

Every chat turn — success, rejection, or error — is written to a local SQLite file:
question, resolved entities, generated SQL, status, error, row count, response
shown. Admin-only via `/query-log`.

## 4. How Chat-with-PDFs works

Standard retrieval-augmented generation, but with unusually careful citations.

1. **Ingest** — text is extracted **line by line with its position on the page**,
   falling back to OCR for scanned pages (Tesseract *or* Google Cloud Vision,
   selectable; degrades gracefully if one is unavailable).
2. **Positions are stored as fractions of the page, not absolute points** — so the
   highlight lands on the right line whatever size the viewer renders at. This is the
   detail that makes the feature feel precise rather than approximate.
3. **Chunk** — consecutive lines grouped into small passages: tight enough to
   highlight, big enough to retrieve well.
4. **Embed and cache per PDF** — change one PDF and only that one is reprocessed.
5. **Answer** — the eight most relevant passages are retrieved; the model must answer
   *only* from them and cite each fact, and must say so when the answer isn't there.
   Citations are renumbered to display order, so a single-source answer shows `[1]`
   not `[4]`.
6. Each citation maps back to a page and line positions, so the UI can open and
   highlight it.

## 5. Stack and layout

```
Browser (React) ──HTTPS──▶ FastAPI backend ──read-only SQL──▶ BigQuery
                              │  auth / roles (JWT)
                              │  NL-to-SQL (GOVERNANCE.md + OpenAI)
                              │  SQL safety (SELECT-only, PII, sqlglot)
                              │  semantic (LGD geography, time)
                              └  query log (SQLite)
```

- **Frontend** — React + TypeScript + Vite + Tailwind; Recharts for charts. Notably
  the chart-decision logic is a **pure, unit-tested module** (chart type, pivoting,
  top-N + "Other", horizontal vs vertical, label formatting) with the renderer kept
  thin — so the visual decisions are provably correct.
- **Backend** — FastAPI, one module per concern: `auth`, `chat`, `nl_to_sql`,
  `sql_safety`, `semantic`, `db`, `query_log`, `report`, `explorer`, `pdfchat`.
- **External** — BigQuery (data), OpenAI (LLM + embeddings), SQLite (query log),
  optional Google Drive (PDF corpus), optional Cloud Vision (OCR).
- **Deploy** — frontend builds to static files (GitHub Pages in the reference setup);
  backend runs under systemd behind nginx.

---

# Part 2 — Issues

Each entry says **what's wrong**, **what it causes**, and **how to fix it** — in
words, not code.

**Verification status:** items marked ✅ **Confirmed** were reproduced by running
code against this repo. Items marked 🔍 **Read from code** were traced through the
source but not executed (they depend on deployment or timing rather than logic).
See the appendix for what was run.

---

## P0 — Fix before any real deployment

### Issue 1 — Running two workers breaks conversation memory 🔍

**What's wrong.** The deployment starts the backend as **two separate processes**
(`deploy/nha-copilot.service:11`, documented at `deploy/SELF_HOSTING.md:231`). But
four things are stored in each process's own memory rather than anywhere shared:
chat sessions, the Explorer cache, the PDF search index, and the user list.

**What it causes.** nginx sends each request to whichever process is free, so your
second question often lands on a process that has never heard of you. Follow-ups
like *"what about Andhra Pradesh?"* intermittently lose all context and the system
asks you to start over. Session-history lookups return "not found" for sessions that
plainly exist. The Explorer and PDF index get built twice, doubling that spend.

Roughly half of all follow-up questions are affected — enough that users would read
it as "the tool is flaky" rather than as a bug with a cause.

**How to fix it.** Two options:

- **Now, one line:** run a single worker instead of two, and add the flags that let
  the app see the real client IP through nginx. This trades concurrency for
  correctness, which is almost certainly the right call for a prototype audience.
- **Properly:** move chat sessions out of process memory into the SQLite database
  the query log already uses, give them an expiry, and treat the PDF index as a file
  built once and loaded at startup rather than per-process. This is a contained
  change but touches three files, because the code currently relies on editing an
  in-memory object and would need to explicitly save instead.

---

### Issue 2 — `SELECT *` slips past both the privacy check and the role check ✅

**What's wrong.** Both the PII scan and the role check work the same way: they list
the column names the query mentions, and compare that list against a blocklist. A
query that says `SELECT *` mentions **no column names at all** — so both lists come
back empty and both checks find nothing to object to.

**Confirmed by running it.** Side by side, against the real code:

| Query | Validator | `viewer` allowed? |
|---|---|---|
| `SELECT facility_name FROM …` | passes | ❌ **blocked** (correct) |
| `SELECT * FROM …` | passes | ✅ **allowed** ← the gap |
| `SELECT t.* FROM … t` | passes | ✅ **allowed** ← the gap |
| `SELECT abha_address FROM …` | **rejected** (correct) | — |
| `SELECT * FROM …` (same table) | passes | ← PII backstop bypassed |

**What it causes.** A `viewer` — the most restricted role, meant to see only
national and state totals — asks *"show me everything in the facility registry"*.
The model writes `SELECT * FROM health_facility_registry LIMIT 50`, the check finds
nothing to block, and the viewer receives individual facility names and addresses
that their tier explicitly forbids. The same hole would hand back `abha_address` if
a future data refresh reintroduced that column, which is exactly the scenario the
backstop exists for.

**How to fix it.** Reject `SELECT *` outright and require the query to name its
columns. This is good practice for a cost-capped BigQuery app anyway, and is the
simpler of the two possible fixes (the alternative — expanding the star against the
known schema before checking — is more code and more ways to be wrong).

> ### ⚠️ There is a trap in this fix — verified
> The obvious approach is "reject any query containing a `*`". **Do not do that.**
> `COUNT(*)` also contains a `*` internally, so a blanket search rejects the single
> most common aggregate in this dataset. I tested this: a naive fix **wrongly
> rejects 11 of 18 legitimate queries**, including four in the weekly report and
> five assertions in the existing test suite.
>
> The fix must look only at the query's **output column list**, not everywhere in
> the query. I verified a version that does this: **7 of 7** dangerous cases
> rejected, **18 of 18** legitimate ones still accepted — including `COUNT(*)`, and
> including a harmless `*` inside a subquery whose outer query does name its columns.

Two things should accompany the fix so it doesn't just produce failures: add a line
to `GOVERNANCE.md` telling the model never to use `SELECT *`, and let a rejection go
through the corrective-retry path that already exists for query errors, so an
occasional slip repairs itself instead of surfacing as "I was unable to answer that."

---

### Issue 3 — The role check lets a query through when it can't understand it ✅

**What's wrong.** If the role checker can't parse a query, it allows it. The comment
says this is because the validator already parsed it successfully — but the two
layers call the parser differently, so they can legitimately disagree.

**Confirmed by running it.** Three unparseable inputs, including `"this is not sql
at all"`, all came back **allowed** for a `viewer`.

**What it causes.** A security control that gives up and says yes. Nothing currently
exploits this — the validator catches most malformed SQL first — but it is the wrong
default for a permission check, and it removes the independence that the whole
three-layer design depends on.

**How to fix it.** Deny instead of allow when the query can't be parsed, and return
the same polite scoped message the user already sees for other role limits. Two
lines. While there, fix a small related bug: the field that reports *which* columns
were blocked is declared as a list but actually returns nothing, which was visible
in the test output above.

---

### Issue 4 — Nothing stops the app booting with the demo password 🔍

**What's wrong.** The signing key for login tokens has a built-in default value —
`"change-me-to-a-long-random-string"` — and the app ships demo accounts whose
passwords are the username plus `123` (so `admin` / `admin123`). If a deployment
forgets to set the two relevant environment variables, it gets **both**, and the
only warning is a line in the log file.

**What it causes.** Anyone who has read this repository knows the default signing
key and the demo passwords. On a misconfigured deployment they can log in as admin,
or forge an admin token without logging in at all. The deploy docs do say to set
these (`deploy/SELF_HOSTING.md:170` and the checklist at `:397`) — but documentation
is not a control, and this is precisely the kind of step that gets missed under time
pressure.

**How to fix it.** Have the app **refuse to start** if the signing key is still the
default (or is too short) or if no real user accounts are configured, with an
opt-out flag for local development. Roughly ten lines, and it converts a silent
compromise into an obvious failure at deploy time.

---

## P1 — Correctness, cost, and abuse

### Issue 5 — One user can resume another user's conversation ✅

**What's wrong.** When a chat request arrives carrying a session ID, the code hands
back that session **without checking it belongs to the person asking**. Notably, the
*other* endpoint that reads session history does check ownership — so the two paths
disagree, which suggests this is an oversight rather than a decision.

**Confirmed by running it.** I created a session as "alice", added a message to it,
then requested the same session ID as "bob". Bob received alice's exact session and
could read her message history.

**What it causes.** A user with someone else's session ID sees their conversation
history, and their own questions get appended to that history. Session IDs are
random UUIDs so this isn't trivially guessable, but it is still a cross-user data
leak with a three-line fix.

**How to fix it.** Treat a session belonging to someone else as if it didn't exist,
and issue a fresh session instead. Important detail: don't reuse the supplied ID in
that case, or you would overwrite the original owner's session.

---

### Issue 6 — The rate limit is shared by everyone, not per user 🔍

**What's wrong.** The limit is counted per client IP address. nginx does forward the
real client IP, but the app isn't started with the flag that tells it to trust that
header — so as far as the app is concerned, every request in the world comes from
the same address: the proxy.

**What it causes.** The README advertises "60 requests per minute per user". In
reality it is 60 per minute **shared across all users combined**. Ten officials
working at once will throttle each other, and it will look like the tool randomly
stops responding.

**How to fix it.** Count the limit against the logged-in user from their token
rather than their IP address. That is the correct unit for a per-request-cost LLM
endpoint anyway, and it removes the dependency on proxy configuration entirely.

---

### Issue 7 — The weekly report has no limit and is expensive 🔍

**What's wrong.** The weekly report endpoint has no rate limit, and each call fires
**about twenty separate database queries** plus an AI-written summary.

**What it causes.** Any logged-in user can request it in a loop and run up real
Google Cloud and OpenAI charges. Each individual query is capped at 2 GB scanned,
which puts the ceiling around 40 GB of scanning **per request**.

**How to fix it.** Add a rate limit, and cache the result for each date range — a
past week's numbers don't change, so rebuilding them on every view is pure waste.
Note one gotcha: the rate-limiting library used here requires the handler to accept
the request object explicitly, which is very likely why this endpoint has no limit
today and why adding one naively appears to break it.

---

### Issue 8 — Simultaneous first-time users each pay full price 🔍

**What's wrong.** The Explorer cache and the PDF index are both built on first use,
with no lock to stop several requests building the same thing at once.

**What it causes.** Three people opening the Explorer tab at the same moment on a
cold cache each run the full generation — roughly ten AI calls plus a database query
per card, so about thirty AI calls and eighteen queries to produce one tab's worth
of content that one build would have covered. On the PDF side, simultaneous cold
requests each re-process and re-embed the entire document corpus, which is the most
expensive operation in the whole application.

**How to fix it.** Let only one build run at a time, and have the others re-check the
cache when they get their turn — by then the work is already done. A standard
pattern, a few lines in each of the two places.

---

### Issue 9 — Explorer and PDF chat don't reach the audit log 🔍

**What's wrong.** Explorer runs its questions through the pipeline directly,
skipping the logging step that chat performs. PDF chat logs nothing at all.

**What it causes.** The design designates the query log as *the* audit trail and as
the primary raw material for improving `GOVERNANCE.md`. A whole category of
generated SQL is therefore invisible to review — and Explorer's SQL is exactly the
adventurous, join-heavy kind most worth reviewing. Failed Explorer queries, the most
informative ones of all, vanish silently.

**How to fix it.** Log from inside the shared pipeline rather than from the chat
endpoint, and tag each row with where it came from (`chat` / `explorer` / `report`).
If a smaller change is preferred, have Explorer log for itself — about ten lines —
placed so that failures are logged too, not just successes.

---

### Issue 10 — Database connections are never closed ✅

**What's wrong.** Every write to the query log opens a database connection using a
pattern that looks like it cleans up after itself, but in Python this particular
pattern saves the data and **does not close the connection**. This is a well-known
language gotcha rather than a careless mistake.

**Confirmed by running it.** After the block that should have closed it, the
connection was still usable — proving it stayed open. I also confirmed the corrected
pattern both closes the connection *and* still saves the data.

**What it causes.** One leaked file handle per chat turn, reclaimed only when
Python's garbage collector happens to get to it. A long-running server under steady
use will accumulate them.

**How to fix it.** Wrap the connection in the standard "close when done" helper at
the three places it's opened. The explicit save calls then become unnecessary. Three
lines.

---

### Issue 11 — Nothing caps how many rows come back ✅

**What's wrong.** There is no limit anywhere on result size. The 2 GB cap limits how
much data BigQuery **scans**, not how much it **returns**.

**Confirmed by running it.** A query with no row limit passes validation unchanged.

**What it causes.** *"List every registered facility"* can return a very large result
set, which is then converted to JSON, sent to the browser, and rendered into a table.
It also interacts with the insight-generation step, which sends up to 50 rows back to
the AI.

**How to fix it.** When the generated query has no limit of its own, add one — say
5,000 rows — and tell the user the result was truncated so they know to narrow the
question. I verified a helper that does this correctly across all the query shapes
this app produces, including multi-part queries, and which safely leaves a query
alone if anything about it is unexpected.

---

### Issue 12 — "Today" is the server's date, not India's 🔍

**What's wrong.** Relative dates are resolved against the server's clock rather than
Indian time.

**What it causes.** On a UTC-hosted server, *"how many ABHAs were created today?"*
asked at 3 a.m. IST resolves to **yesterday** in India. Silently wrong answers to a
very natural question — the worst failure mode for a tool whose whole value is
trust in its numbers.

**How to fix it.** Resolve "today" explicitly in India Standard Time. The data is
Indian; the clock should be too.

**Verified prerequisite:** Windows does not ship a timezone database, and I
confirmed that asking for `Asia/Kolkata` on this machine **fails outright**. So this
fix requires adding the `tzdata` package to the requirements file — it is not
optional, and skipping it would turn a wrong answer into a crash.

---

## P2 — Quality, cost, and maintenance

### Issue 13 — The pinned dependencies don't install on the newest Python ✅

**What's wrong.** `requirements.txt` pins a version of `pydantic` that has no
prebuilt package for Python 3.14. Installation tries to compile it from source,
which needs a Rust toolchain, which then fails behind a corporate network.

**Confirmed by running it.** `pip install -r requirements.txt` fails on this
machine, which has Python 3.14. Installing a newer `pydantic` (2.13) works fine.

**What it causes.** A new developer on a current Python gets a wall of compiler
errors during setup. The README says "Python 3.11+, verified on 3.13", so this is
more a documentation gap than a defect — but it will cost someone an afternoon.

**How to fix it.** Either raise the `pydantic` pin to a version with Python 3.14
support, or state the supported Python range explicitly in the README and the setup
script. The second is less work and less risk.

### Issue 14 — The architecture doc describes a system that no longer exists

*(Since fixed — see `issues.md` #14.)*

`docs/architecture.md` carried a clear "SUPERSEDED" banner, which was good — but it
was still 700 lines describing PostgreSQL, PM-JAY claims tables, and clinical
specialty codes, none of which are part of this project any more. A newcomer, or an
AI assistant pointed at the repo, would absorb the wrong data model. It now lives at
`docs/history/architecture-pmjay-superseded.md`, with a documentation map at
`docs/README.md` that states plainly which documents are authoritative.

### Issue 15 — Dead code ✅

Confirmed unused by search: the save/load methods on the PDF vector store (the
service uses its own per-document caching instead), and the "dry run" query method
on the BigQuery client. Deleting the first means the caching story has one obvious
home. The second is worth keeping — see Optimization 5.

### Issue 16 — The PDF cache can be corrupted by a crash

The entire document corpus is written to one file in a single step. If the process
dies mid-write, the file is left truncated. Recovery is graceful — the app notices
and rebuilds — but rebuilding means re-embedding everything, which is the expensive
operation. Writing to a temporary file and then renaming it makes the swap atomic;
a crash then leaves the previous good copy intact.

### Issue 17 — Test coverage stops at the oldest modules

The test suite covers the validator, the role check, the geography layer, and the
pipeline; the frontend covers the chart engine. **Nothing covers the weekly report,
the Explorer, or any of the PDF chat** — which is where the newest and most
expensive logic lives.

Worth noting: **a three-line test asserting that a `SELECT *` query is denied to a
viewer would have caught Issue 2.** Highest-value additions, in order: `SELECT *`
handling, the citation-renumbering logic in PDF chat, and the number-formatting
helpers in the weekly report.

### Issue 18 — Smaller items

| Item | Where | Note |
|---|---|---|
| Deprecated startup hook | `main.py:51` | The startup-event style used here is deprecated in current FastAPI; it will need migrating eventually |
| Unbounded log query | `query_log/router.py:13` | Asking for a huge number of log rows scans the whole table. Admin-only, so low severity. Clamp it |
| Password truncation | `auth/users.py:22` | Passwords are cut at 72 bytes, which can slice a multi-byte character in half. Harmless today, latent oddity |
| Login token in browser storage | `frontend/src/App.tsx:14` | Readable by any injected script. Acceptable prototype trade-off; worth stating in the security notes |
| 8-hour token, no renewal | `config.py:57` | A long analyst session simply dies mid-work with no refresh path |
| Shallow health check | `main.py:72` | Reports "ok" without checking anything, so a monitor shows green while every query fails on expired credentials |

---

## Suggested order of work

| # | Change | Effort | Payoff |
|---|---|---|---|
| 1 | Single worker, or move sessions to shared storage | 1 line / 1 day | Fixes intermittent context loss — the most user-visible bug |
| 2 | Reject `SELECT *` (output column list only — mind the trap) | ~30 min | Closes the role + privacy gap |
| 3 | Refuse to boot with default secrets | ~30 min | Silent compromise becomes a loud failure |
| 4 | Role check denies on parse failure | 2 lines | Removes a fail-open security path |
| 5 | Ownership check on session resume | 3 lines | Closes the cross-user history leak |
| 6 | Rate-limit per user; add a limit to the weekly report | ~1 hour | Makes the documented limits real; caps spend |
| 7 | Locks around Explorer and PDF index builds | ~1 hour | Removes duplicated cost |
| 8 | Close database connections properly | 3 lines | Stops the handle leak |
| 9 | Cap returned rows | ~1 hour | Protects the browser and payload size |
| 10 | Run the weekly report's queries in parallel | ~half day | 20–40 s → a few seconds |

Items 2–5 and 8 are five small, independent, low-risk changes that together close
every security finding in this report. They are worth doing as one batch.

---

# Part 3 — Optimizations

These change no behaviour — only cost and speed.

### Optimization 1 — Run the weekly report's queries in parallel

**The single biggest speed win available.** The report issues about twenty database
queries one after another, each waiting on the previous. They are all independent of
each other, and each spends most of its time waiting rather than computing.

Running them concurrently should take the report from **20–40 seconds down to a few
seconds**. Combined with caching the result per date range (Issue 7), repeat views
become instant. Half a day's work for the most noticeable improvement in the app.

### Optimization 2 — Speed up PDF search

Similarity scoring currently runs as a plain Python loop over every passage on each
search. Using a numerical library to do it as a single matrix operation is both
faster and less code, and the scoring values can be pre-computed once when the index
is built rather than on every query.

At the current corpus size this isn't the bottleneck — take it when the corpus grows,
or take it now because it also simplifies the code.

### Optimization 3 — Make the PDF cache write atomic

Covered as Issue 16. Write to a temporary file and rename it, so a crash can't
destroy a good cache and force a full re-embed.

Related: the embeddings are currently stored as text numbers, which is roughly ten
times the size of the equivalent binary format. If the corpus grows, keep the
descriptive data as text and move the numbers to a binary sidecar file.

### Optimization 4 — Delete the dead code

Covered as Issue 15. Two small removals that make the caching design easier to follow.

### Optimization 5 — Reuse the unused "dry run" for better error messages

The BigQuery client has a "dry run" method that validates a query and estimates its
size **without running it and without ever being billed**. It is currently never
called.

Using it before the real query would catch wrong column names for free, and would
let the app say *"that query would scan 340 GB — try narrowing it"* instead of
surfacing an opaque failure when the 2 GB cap trips.

**Honest trade-off:** it adds a round-trip (a few hundred milliseconds) to **every
successful** request in order to improve the unsuccessful ones. Since the billing cap
already limits the damage, treat this as a message-quality improvement rather than a
cost saving, and only take it if users are actually hitting confusing failures.

### Optimization 6 — Considered and rejected: rewriting non-English column labels locally

When the model labels a column in Hindi script, BigQuery rejects the query and the
app spends an entire extra AI call regenerating it. Fixing those labels locally looks
like free money.

**It isn't safe.** The chart definition and the answer text both refer to columns *by
the label the model chose*. Renaming a label locally would silently break chart
rendering and the written answer. The existing regeneration is the correct approach.

The cheap win is on the prompt side instead: make the "labels must be plain English"
rule prominent in `GOVERNANCE.md` with one worked Hindi example, so the extra
round-trip is rarely needed at all. That costs nothing per request.

### Optimization 7 — The prompt is already cost-efficient; don't regress it

`GOVERNANCE.md` is ~30 KB (roughly 8,000 tokens) and is sent on every query. That
sounds expensive, but the code already has this right: the rulebook is byte-for-byte
identical on every request, and everything that changes per request — the question,
the resolved dates and places, the conversation history — correctly goes in a
separate part of the message.

That means OpenAI serves the expensive, unchanging part from its cache at a
discount. **The thing to avoid** is moving per-request data into the rulebook section
as a convenience: that would break the cache on every single request and multiply
prompt cost. Worth a comment in the code so nobody does it later.

### Optimization 8 — Cache repeated questions

Explorer regenerates the same popular questions, and officials re-ask the same things
across sessions. Caching answers for a short period, keyed on the question **and the
user's role** (since roles change what a query may return), would cut both AI and
database cost on the most common path.

Two rules to get right: only cache successful answers, and never cache anything whose
date range is relative — "today" and "this week" go stale, and a stale answer is
worse than a slow one.

### Optimization 9 — Frontend bundle: mostly already done ✅

Credit where due — I checked, and the heavy libraries are **already** loaded on
demand rather than upfront: the Excel export, the PowerPoint export, and the PDF
viewer are all deferred until actually used. The static-looking imports elsewhere
only pull in thin wrapper files, not the libraries themselves.

The one remaining candidate is the charting library (~500 KB), which is loaded
upfront even though no chart exists until the first answer arrives. Same treatment as
the PDF viewer already gets.

**Measure before doing it** — build the frontend and check the reported chunk sizes
to confirm it's worth the added complexity.

### Optimization 10 — Make the health check mean something

The health endpoint returns "ok" unconditionally, so a monitor reports green while
every query fails. Startup already tries to load the geography data and the live
database schema, and already swallows both failures with a log warning — so the
information exists and is being thrown away.

Recording those two outcomes and reporting them costs nothing. Keep the check free of
credentials and side effects — don't run a database query on every health poll.

---

# Part 4 — What this project gets right

Worth recording alongside the issues, because these are the parts not to disturb:

- **`GOVERNANCE.md` as a first-class, version-controlled artifact.** Encoding the
  counting rules, the coded values, the awkward date columns, and the join fan-out
  trap as *explicit written rules* is what makes free-form question-to-SQL
  trustworthy. Most projects of this shape skip it and quietly return wrong numbers.
- **Resolving places and dates before the AI sees them.** Place names and date
  phrases are exactly where a language model guesses badly, and exactly where a
  lookup table is cheap and exact.
- **Defence in depth that doesn't trust the prompt.** The validator would reject a
  dangerous query even if the prompt were fully subverted, and the database
  permissions would reject it even if the validator were bypassed. Which is why
  Issue 2 is worth fixing promptly — it's a gap in a model that is otherwise sound.
- **Honesty about data coverage.** Encoding "only Bihar and Andhra Pradesh have
  activity data" as a rule, so the answer is *"I don't have that"* rather than a
  misleading zero, is the right call and easy to get wrong.
- **The chart engine as a pure, tested module.** Visual decisions are provably
  correct and reviewable without opening a browser.
- **Citation precision in PDF chat.** Storing positions as page fractions rather than
  absolute points, and renumbering citations to display order, are small decisions
  that make the feature feel exact instead of approximate.
- **Clean seams for the things most likely to change.** The AI client, the PDF source,
  and the OCR engine are all swappable without touching application logic — which
  matters given the stated requirement to move off OpenAI to an India-hosted model
  later.

---

# Appendix — How these findings were verified

Environment: Python 3.14.7, `sqlglot` 26.0.1 (the version pinned in
`requirements.txt`), in a fresh virtual environment at `backend/.venv`.

**Baseline:** the existing test suite was run first — `test_validator.py`,
`test_rbac.py`, `test_semantic.py`: **25 passed**. So every finding below is against
a codebase whose own tests are green.

| Finding | How it was checked | Result |
|---|---|---|
| Issue 2 — `SELECT *` bypass | Ran the validator and role check over star queries vs explicit-column controls | ✅ Confirmed: star allowed where the explicit column is blocked |
| Issue 2 — the `COUNT(*)` trap | Applied the naive fix to 18 legitimate queries drawn from the test suite and the weekly report | ✅ Confirmed: would wrongly reject 11 of 18 |
| Issue 2 — the correct fix | Tested an output-column-list-only version against 7 dangerous and 18 legitimate queries | ✅ 7/7 rejected, 18/18 accepted |
| Issue 3 — role check fails open | Passed three unparseable strings to the role check as a `viewer` | ✅ Confirmed: all allowed |
| Issue 5 — session hijack | Created a session as "alice", added history, requested that ID as "bob" | ✅ Confirmed: bob got alice's session and history |
| Issue 10 — connection leak | Used the connection after its cleanup block, then compared against the corrected pattern | ✅ Confirmed leak; fix verified to close *and* save |
| Issue 11 — no row cap | Validated a query with no row limit | ✅ Confirmed: passes unchanged |
| Issue 12 — timezone prerequisite | Requested the `Asia/Kolkata` timezone on this machine | ✅ Confirmed it fails — `tzdata` is required, not optional |
| Issue 13 — install failure | Ran `pip install -r requirements.txt` on Python 3.14 | ✅ Confirmed failure; newer `pydantic` resolves it |
| Issue 15 — dead code | Searched the codebase for callers | ✅ Confirmed: no callers |
| Optimization 9 — bundle already split | Searched for how the heavy libraries are imported | ✅ Already deferred — no change needed |

The remaining items (Issues 1, 4, 6, 7, 8, 9, 14, 16, 17, 18) were traced through the
source but not executed, because they depend on deployment configuration, request
timing, or live cloud credentials rather than on logic that can be tested locally.
They are marked 🔍 above. Each names the file and line where the behaviour originates,
so they can be confirmed on a deployed instance.
