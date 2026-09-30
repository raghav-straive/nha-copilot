# Issues — NHA Analytics Co-Pilot

Every issue found in a review of this codebase, in plain language. For each one:
**what's wrong**, **what it causes**, and **what was done about it**.

For what the project *is* and how it works, see [`brief.md`](brief.md).
For speed and cost improvements, see [`optimizations.md`](optimizations.md).

---

## Status legend

| Mark | Meaning |
|---|---|
| ✅ **Fixed** | Fixed in this repository, with a test where the behaviour is testable |
| ⬜ **Open** | Still outstanding — a judgement call, or needs a deployed instance |

**How each issue was checked:**

| Mark | Meaning |
|---|---|
| 🧪 **Reproduced** | I ran code against this repo and watched it happen |
| 📖 **Read from code** | Traced through the source, not executed (depends on deployment, timing, or live cloud credentials) |

---

## Summary

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | Running two workers breaks conversation memory | P0 | ✅ Fixed |
| 2 | `SELECT *` slips past the privacy and role checks | P0 | ✅ Fixed |
| 3 | Role check allows a query it can't understand | P0 | ✅ Fixed |
| 4 | Nothing stops the app booting with the demo password | P0 | ✅ Fixed |
| 5 | One user can resume another user's conversation | P1 | ✅ Fixed |
| 6 | The rate limit is shared by everyone, not per user | P1 | ✅ Fixed |
| 7 | The weekly report has no limit and is expensive | P1 | ✅ Fixed |
| 8 | Simultaneous first-time users each pay full price | P1 | ✅ Fixed |
| 9 | Explorer and PDF chat don't reach the audit log | P1 | ✅ Fixed |
| 10 | Database connections are never closed | P1 | ✅ Fixed |
| 11 | Nothing caps how many rows come back | P1 | ✅ Fixed |
| 12 | "Today" is the server's date, not India's | P1 | ✅ Fixed |
| 13 | Dependencies don't install on the newest Python | P2 | ✅ Fixed |
| 14 | The architecture doc describes a system that no longer exists | P2 | ✅ Fixed |
| 15 | Dead code | P2 | ✅ Fixed |
| 16 | The PDF cache can be corrupted by a crash | P2 | ✅ Fixed |
| 17 | Test coverage stops at the oldest modules | P2 | ✅ Fixed |
| 18 | Deprecated app-startup mechanism | P2 | ✅ Fixed |
| 19 | Password truncation could merge two passwords | P2 | ✅ Fixed |
| 20 | Login tokens expire with no way to renew | P2 | ✅ Fixed (backend) |
| 21 | Unbounded audit-log query | P2 | ✅ Fixed |
| 22 | Health check reported "ok" regardless | P2 | ✅ Fixed |
| 23 | Test dependencies missing from the manifest | P2 | ✅ Fixed |
| 24 | The JWT library is unmaintained | P2 | ✅ Fixed |
| 25 | Login token readable by injected scripts | P2 | ✅ Fixed |
| 26 | **A letter "q" anywhere in a question silently dropped the year** | **P1** | ✅ Fixed |
| 27 | **An explicit date was misread as a financial year** | **P1** | ✅ Fixed |
| 28 | Tests never ran in CI | P2 | ✅ Fixed |
| 29 | 14 dependency vulnerabilities, 1 critical | P2 | ✅ Fixed (10 of 14) |
| 30 | Replacing a PDF served a stale page image | P2 | ✅ Fixed |
| 31 | **Column totals vanished on core metrics** | **P1** | ✅ Fixed |
| 32 | **Rates and averages were being summed** | P2 | ✅ Fixed |
| 33 | A tall word merged two OCR lines into one | P2 | ✅ Fixed |
| 34 | PDF page images leaked memory | P2 | ✅ Fixed |
| 35 | Dead parameter in the OCR entry point | P2 | ✅ Fixed |
| 36 | **55 district names answered about the wrong district** | **P1** | ✅ Fixed |
| 37 | Place detection re-resolved by the wrong name | P1 | ✅ Fixed |
| 38 | **Grouped charts dropped categories silently** | P2 | ✅ Fixed |
| 39 | An internal sort key leaked into chart rows | P3 | ✅ Fixed |
| 40 | Excel export: merge stopped at column Z; widths ignored labels | P3 | ✅ Fixed |
| 41 | Two dozen districts filed under an empty name | P3 | ✅ Fixed |
| 42 | **Tables showed raw codes while charts showed names** | P2 | ✅ Fixed |
| 43 | The two Excel buttons produced different files | P2 | ✅ Fixed |
| 44 | Horizontal bar charts exported as vertical columns | P3 | ✅ Fixed |
| 45 | Dead-code checks were switched off, hiding real dead code | P3 | ✅ Fixed |

**Tests: 25 → 195 backend + 98 frontend (293 total).** Every issue marked fixed
has a test, except the deployment-configuration ones (1, 6) which have no local
equivalent.

**Nothing is left open.** Issues 26–41 came from later sweeps once both
toolchains were available. The pattern across them is worth naming: **#2, #26,
#27, #31, #36 and #38 were all silent — no error, no crash, just a quietly wrong
number, a missing one, or a chart that omitted data and looked complete.** That
is the failure mode that matters most for a tool whose entire value is trust in
its figures, and none of it was visible without running the code.

---

# P0 — Must fix before real deployment

## Issue 1 — Running two workers breaks conversation memory

📖 Read from code · ✅ **Fixed**

**What's wrong.** The deployment ran the backend as **two separate processes**. But
four things lived in each process's own memory rather than anywhere shared: chat
sessions, the Explorer cache, the PDF search index, and the user list.

**What it caused.** The web server sends each request to whichever process is free,
so your second question often landed on a process that had never heard of you.
Follow-ups like *"what about Andhra Pradesh?"* lost all context roughly **half the
time**, and session lookups returned "not found" for sessions that plainly existed.
Users would read this as "the tool is flaky" rather than as a bug with a cause.

**What was done.** Chat sessions moved out of process memory into a shared SQLite
file, with a 7-day expiry and a cap on stored history. Two workers is now safe.
Tests cover a session written by one store instance and read back by another,
which stands in for "a second worker handles the next turn".

The Explorer cache also now persists to disk, so a second worker (or a restart)
reuses the cards instead of regenerating them.

> ### A correction to an earlier version of this document
> An earlier draft said the Explorer cache *and* the PDF index were both
> regenerated per worker, "doubling that spend". I checked, and that was too
> strong for the PDF index: its cache is on disk and keyed per document, so a
> second worker re-reads and re-parses the cache file but **does not re-embed
> anything**. The cost there is a little CPU, not AI calls.
>
> The Explorer was the real problem — it had no disk cache at all, so each
> worker genuinely paid full price in AI calls. That is what's now fixed.

---

## Issue 2 — `SELECT *` slips past the privacy and role checks

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** The privacy check and the role check both worked the same way:
list the column names a query mentions, then compare that list against a blocklist.
A query saying `SELECT *` mentions **no column names at all** — so both lists came
back empty and both checks found nothing to object to.

**Reproduced, side by side against the real code:**

| Query | Validator | `viewer` allowed? |
|---|---|---|
| `SELECT facility_name FROM …` | passes | ❌ **blocked** (correct) |
| `SELECT * FROM …` | passes | ✅ **allowed** ← the gap |
| `SELECT t.* FROM … t` | passes | ✅ **allowed** ← the gap |
| `SELECT abha_address FROM …` | **rejected** (correct) | — |
| `SELECT * FROM …` (same table) | passes | ← privacy backstop bypassed |

**What it caused.** A `viewer` — the most restricted role, meant to see only national
and state totals — asks *"show me everything in the facility registry"*. The model
writes `SELECT * FROM health_facility_registry LIMIT 50`, nothing objects, and the
viewer receives individual facility names and addresses their tier forbids. The same
hole would have returned patient-identifying data if a future data refresh
reintroduced that column — exactly the scenario the backstop exists for.

**What was done.** `SELECT *` and `alias.*` are now rejected, and the query must name
its columns. A rule was added to `GOVERNANCE.md` so the model rarely trips it, and a
rejection now goes through the existing self-repair path so an occasional slip fixes
itself instead of surfacing as "I was unable to answer that".

> ### ⚠️ There was a trap in this fix — and it's verified
> The obvious approach is "reject any query containing a `*`". **That breaks the
> app.** `COUNT(*)` also contains a `*` internally, so a blanket search rejects the
> single most common aggregate in this dataset.
>
> I measured it: the naive fix **wrongly rejects 11 of 18 legitimate queries**,
> including four in the weekly report and five assertions in the existing tests.
>
> The shipped fix looks only at the query's **output column list**, not everywhere
> in the query: **7 of 7** dangerous cases rejected, **18 of 18** legitimate ones
> still accepted — including `COUNT(*)`, and including a harmless `*` inside a
> subquery whose outer query does name its columns.

---

## Issue 3 — Role check allows a query it can't understand

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** If the role checker couldn't make sense of a query, it allowed it.
The reasoning was that the validator had already parsed it successfully — but the two
layers ask the parser in slightly different ways, so they can legitimately disagree.

**Reproduced.** Three unparseable inputs, including the literal text
`"this is not sql at all"`, all came back **allowed** for a `viewer`.

**What it caused.** A security control that gives up and says yes. Nothing was
exploiting it — the validator catches most malformed queries first — but it's the
wrong default for a permission check, and it quietly removed the independence that
the whole three-layer safety design depends on.

**What was done.** It now denies instead, with the same polite message users already
see for other access limits. A small related bug was fixed at the same time: the
field reporting *which* columns were blocked was declared as a list but actually
returned nothing, so logs printed "None".

---

## Issue 4 — Nothing stopped the app booting with the demo password

📖 Read from code · ✅ **Fixed**

**What's wrong.** The signing key for login tokens had a built-in default value, and
the app shipped demo accounts whose passwords were the username plus `123` (so
`admin` / `admin123`). A deployment that forgot to set two environment variables got
**both**, and the only warning was a line in a log file.

**What it caused.** Anyone who has read this repository knows the default signing key
and the demo passwords. On a misconfigured deployment they could log in as admin — or
forge an admin token without logging in at all. The deploy docs did say to set these,
but documentation is not a control, and this is exactly the step that gets missed
under time pressure.

**What was done.** The app now **refuses to start** if the signing key is still the
default or is too short, or if no real accounts are configured. Setting
`ALLOW_INSECURE_DEV=1` downgrades the refusal to a warning for local work. Verified
both ways: the guard fires when the flag is off and is bypassed when it's on.

---

# P1 — Correctness, cost, and abuse

## Issue 5 — One user could resume another user's conversation

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** When a chat request arrived carrying a session ID, the code handed
back that session **without checking it belonged to the person asking**. The *other*
endpoint that reads session history did check — so the two disagreed, which is what
made this look like an oversight rather than a decision.

**Reproduced.** I created a session as "alice", added a message, then asked for the
same session ID as "bob". Bob received alice's exact session and read her history.

**What it caused.** Anyone holding someone else's session ID saw their conversation,
and their own questions were appended to it. Session IDs are random, so it wasn't
easily guessable — but it was still a cross-user data leak.

**What was done.** A session belonging to someone else is now treated as if it didn't
exist, and a fresh one is issued. Importantly the supplied ID is *not* reused in that
case, which would have overwritten the original owner's session. Three tests cover
this, including one asserting the owner's session survives untouched.

---

## Issue 6 — The rate limit was shared by everyone, not per user

📖 Read from code · ✅ **Fixed**

**What's wrong.** The limit was counted per client network address. The web server
does forward the real visitor's address, but the app wasn't told to trust that — so as
far as the app was concerned, every request in the world came from the same place.

**What it caused.** The README advertises "60 requests per minute per user". In
reality it was 60 per minute **shared across all users combined**. Ten officials
working at once would throttle each other, and it would look like the tool randomly
stopped responding.

**What was done.** The limit now counts against the logged-in user from their login
token. That's the right unit for a per-request-cost AI endpoint anyway, and it removes
the dependence on web-server configuration entirely. Verified: a request with a token
keys to `user:analyst`; one without falls back to the address.

---

## Issue 7 — The weekly report had no limit and was expensive

📖 Read from code · ✅ **Fixed**

**What's wrong.** The weekly report had no rate limit, and each call fired **about
twenty separate database queries** plus an AI-written summary.

**What it caused.** Any logged-in user could request it in a loop and run up real
cloud and AI charges. Each query is capped at 2 GB scanned, putting the ceiling around
**40 GB of scanning per request**.

**What was done.** Limited to 6 requests per minute, and the result is cached for six
hours per date range — a past week's numbers don't change, so rebuilding them on every
view was pure waste.

> **A gotcha worth knowing:** the rate-limiting library used here requires the handler
> to accept the request object explicitly. That is almost certainly why this endpoint
> had no limit before — adding one naively appears to break the endpoint.

---

## Issue 8 — Simultaneous first-time users each paid full price

📖 Read from code · ✅ **Fixed**

**What's wrong.** The Explorer cache and the PDF index were both built on first use,
with nothing to stop several requests building the same thing at once.

**What it caused.** Three people opening the Explorer tab at the same moment on a cold
cache each ran the full generation — roughly ten AI calls plus a database query per
card. So about **thirty AI calls and eighteen queries** to produce one tab's worth of
content that one build would have covered. On the PDF side, simultaneous cold requests
each re-processed the entire document corpus — the most expensive operation in the app.

**What was done.** Only one build runs at a time now; the others re-check the cache
when they get their turn, by which point the work is already done.

---

## Issue 9 — Explorer and PDF chat didn't reach the audit log

📖 Read from code · ✅ **Fixed for Explorer**

**What's wrong.** Explorer ran its questions straight through the pipeline, skipping
the logging that normal chat performs. PDF chat logged nothing at all.

**What it caused.** The design designates the query log as *the* audit trail and as
the raw material for improving the rulebook. A whole category of generated SQL was
invisible to review — and Explorer's SQL is exactly the adventurous, join-heavy kind
most worth reviewing. Failed Explorer queries, the most informative ones of all,
vanished silently.

**What was done.** Both now log. Explorer logs every question it runs, deliberately
**before** the success filter so failures are captured too. PDF chat logs the
question, the answer and how many documents were cited (it generates no SQL, so
that field stays empty).

The log also gained a **`source`** column — `chat`, `explorer`, `pdfchat` or
`report` — so the audit trail can tell a user's own question from one the Explorer
invented, and `/query-log?source=explorer` filters to just those. Databases created
before the column existed are migrated automatically on startup, with existing rows
defaulting to `chat`; there's a test that builds an old-format database and checks
the migration.

Logging is wrapped everywhere so it can never break an answer — there's a test for
that too.

---

## Issue 10 — Database connections were never closed

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** Every write to the query log opened a database connection using a
pattern that *looks* like it cleans up after itself. In Python this particular pattern
saves the data but **does not close the connection**. It's a well-known language
gotcha rather than a careless mistake.

**Reproduced.** After the block that should have closed it, the connection was still
usable — proving it stayed open.

**What it caused.** One leaked file handle per chat turn, reclaimed only when Python's
garbage collector happened to get to it. A long-running server under steady use would
accumulate them.

**What was done.** All three places now use the standard "close when done" wrapper.
Verified that the corrected pattern both closes the connection *and* still saves the
data.

---

## Issue 11 — Nothing capped how many rows came back

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** There was no limit anywhere on result size. The 2 GB cap limits how
much the database **scans**, not how much it **returns**.

**Reproduced.** A query with no row limit passed validation unchanged.

**What it caused.** *"List every registered facility"* could return a very large
result, which was then converted to JSON, sent to the browser, and rendered into a
table. It also fed the insight-generation step, which sends rows back to the AI.

**What was done.** When a generated query has no limit of its own, one is added
(5,000 rows), and the answer now says the result was truncated so the user knows to
narrow the question. Tested across every query shape this app produces, including
multi-part queries — and it safely leaves a query alone if anything about it is
unexpected, on the principle that a missing cap beats a corrupted query.

---

## Issue 12 — "Today" was the server's date, not India's

📖 Read from code · ✅ **Fixed**

**What's wrong.** Relative dates were resolved against the server's clock rather than
Indian time.

**What it caused.** On a server running in UTC, *"how many ABHAs were created
today?"* asked before 05:30 IST resolved to **yesterday** in India. Silently wrong
answers to a very natural question — the worst failure mode for a tool whose entire
value is trust in its numbers.

**What was done.** "Today" is now resolved explicitly in India Standard Time. The data
is Indian; the clock should be too.

> **A prerequisite that would have bitten:** Windows doesn't ship a timezone database,
> and I confirmed that asking for the India timezone on this machine **failed
> outright**. So a required package was added to the dependency list. Without it, this
> fix would have turned a wrong answer into a crash.

---

# P2 — Quality and maintenance

## Issue 13 — Dependencies didn't install on the newest Python

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** The dependency list pinned a version of a core library that has no
ready-made package for Python 3.14. Installation tried to compile it from source,
which needs a Rust toolchain, which then failed behind a corporate network.

**Reproduced.** `pip install -r requirements.txt` failed outright on this machine.

**What it caused.** A new developer on a current Python got a wall of compiler errors
during setup. The README says "verified on Python 3.13", so it's more a documentation
gap than a defect — but it would cost someone an afternoon.

**What was done.** The pin was relaxed to a version with Python 3.14 support. Verified:
the full dependency list now installs cleanly.

---

## Issue 14 — The architecture doc describes a system that no longer exists

✅ **Fixed**

**What's wrong.** `docs/architecture.md` carried a clear "SUPERSEDED" banner, which
was good. But it was still 700 lines describing a different database, different
tables, and clinical codes that are no longer part of this project.

**What it caused.** A newcomer — or an AI assistant pointed at the repo — would
absorb the wrong data model. A banner is easy to scroll past.

**What was done.** The file moved to
`docs/history/architecture-pmjay-superseded.md`, and a new `docs/README.md` maps the
documentation: authoritative documents first, then a "do not use as a reference"
section explaining exactly what in the old document is stale and which two sections
are still worth reading for design rationale.

**Why moving rather than trimming.** Deleting content means judging which design
reasoning is worth keeping, and that's the author's call, not a reviewer's. Moving it
under `history/` makes the status *structural* — you can't miss it — without
destroying anything. The file's git history is preserved through the move.

---

## Issue 15 — Dead code

🧪 Reproduced · ✅ **Fixed**

Two things were confirmed unused by searching the codebase: the save/load methods on
the PDF search index (the service has its own per-document caching instead), and a
"dry run" query method.

**What was done.** The unused save/load pair was removed, so the caching design now
has one obvious home. The dry-run method was **kept** — it's genuinely useful, see
Optimization 5 in [`optimizations.md`](optimizations.md).

---

## Issue 16 — The PDF cache could be corrupted by a crash

📖 Read from code · ✅ **Fixed**

**What's wrong.** The entire document corpus was written to one file in a single step.
If the process died mid-write, the file was left half-finished.

**What it caused.** Recovery was graceful — the app noticed and rebuilt — but
rebuilding means reprocessing every document, which is the expensive operation.

**What was done.** The cache is now written to a temporary file and then renamed, which
is an atomic swap. A crash now leaves the previous good copy intact.

---

## Issue 17 — Test coverage stops at the oldest modules

✅ **Fixed**

**What's wrong.** The suite originally covered the validator, the role check, the
geography layer, and the pipeline; the frontend covered the chart engine. **Nothing
covered the weekly report, the Explorer, or any of the PDF chat** — where the newest
and most expensive logic lives.

Worth sitting with: **a three-line test asserting that a `SELECT *` query is denied
to a viewer would have caught Issue 2**, the most serious finding in this review.

**What was done.** Tests went from **25 to 140**:

| Area | What's covered |
|---|---|
| SQL safety | Star projections (plain, qualified, multi-part, through a CTE), `COUNT(*)` regression guards, the row cap across every query shape |
| Access control | Role tiers, failing closed on unparseable SQL, star-vs-validator interaction |
| Sessions | Ownership on resume, the owner's session surviving, persistence across store instances, history cap |
| Weekly report | Number/date helpers, end-date exclusivity, concurrent fetch, one failing query not sinking the report, the district-master fan-out guard |
| Explorer | Caching, force-refresh, per-role separation, disk cache reuse by a fresh process, stale and corrupt cache handling, logging of failures, a broken logger not breaking the build |
| PDF chat | Search ranking, `k` handling, zero vectors, the fast and slow search paths agreeing, citation renumbering (order of appearance, out-of-range dropped, mapping back to the right page), graceful degradation |
| Repeated-question cache | Role in the key, resolved period in the key, expiry, LRU eviction |
| Auth | Long/multibyte/emoji passwords, distinct salts, malformed account config, token round-trip, forged-token rejection |
| Query log | The `source` column, migration of an old-format database, filter, limit clamp |
| HTTP layer | Login, refresh, auth required, admin-only endpoints, cross-user session 404, date validation, compression on and off |

All 140 run offline in about 30 seconds — no cloud credentials, no AI calls.

---

## Issue 18 — Deprecated app-startup mechanism

✅ **Fixed**

The startup hook style used here is deprecated in the current web framework and
would eventually stop working. Migrated to the supported mechanism, which also puts
startup and shutdown in one place. Covered indirectly by the HTTP tests, which boot
the real app.

---

## Issue 19 — Password truncation could merge two different passwords

✅ **Fixed**

**What's wrong.** Passwords were cut to their first 72 bytes before hashing (the
hashing algorithm ignores anything beyond that). Two problems: cutting raw UTF-8 at a
byte boundary can slice a multi-byte character in half, and any two passwords sharing
a 72-byte prefix collapsed into **the same credential**.

**What it caused.** With a long passphrase, a wrong password could be accepted. Only
reachable with passwords over 72 bytes, so unlikely in practice — but it's a silent
authentication weakness.

**What was done.** The password is now condensed with SHA-256 and base64-encoded
before hashing, which is the standard fix: every character contributes, the result is
always within the limit, and no byte-slicing occurs. Tests cover a 73rd-byte
difference, long Hindi text, and emoji.

---

## Issue 20 — Login tokens expired with no way to renew

✅ **Fixed (backend)** · ⬜ **Frontend wiring open**

**What's wrong.** Tokens last 8 hours and there was no renewal path, so a long
analyst session simply died mid-work.

**What was done.** A `POST /auth/refresh` endpoint exchanges a still-valid token for
a fresh one. It deliberately re-reads the role from the user store rather than
copying it from the old token, so a role change — or a removed account — takes effect
at the next refresh instead of persisting until expiry. Tested including the forged-
and missing-token cases.

**The frontend now calls it** — hourly while the tab is open, and once on load to
restore a session. Node was installed to do this, so the change is type-checked and
the production build verified.

---

## Issue 21 — Unbounded audit-log query

✅ **Fixed.** Asking for a huge number of log rows scanned the whole table. Now
clamped, with an index on the timestamp column the log is always sorted by.

---

## Issue 22 — Health check reported "ok" regardless

✅ **Fixed.** It returned "ok" unconditionally, so a monitor showed green while every
query failed on expired credentials. Startup already knew whether the geography data
and the live schema had loaded — it just threw that away. It now reports both plus
whether an AI key is configured, and says `degraded` rather than `ok` when something
is missing. No extra cloud calls per health poll.

---

## Issue 23 — Test dependencies missing from the manifest

✅ **Fixed**

`pytest` was not in `requirements.txt` at all, so the documented test command only
worked if you happened to have it installed globally — and the HTTP tests need
`httpx` as well. Added `requirements-dev.txt` (which includes the main manifest), and
the README now shows the install step and the `ALLOW_INSECURE_DEV=1` flag the tests
need.

---

## Issue 24 — The JWT library was unmaintained

🧪 Reproduced (as a deprecation warning during the test run) · ✅ **Fixed**

**What's wrong.** The project used `python-jose` 3.3.0 for login tokens. It has had
no release in years, and it calls a date function that Python has deprecated and
scheduled for removal.

**What it caused.** Nothing visible — a warning. But it would become an error on a
future Python, and an unmaintained authentication library gets no security fixes.

**What was done.** Migrated to `PyJWT`, which is actively maintained.

**One security improvement came with it.** The decode call now passes an explicit
list of permitted signing algorithms. Without that, a token can name its own
algorithm — including `none`, meaning *no signature at all* — and some libraries
will happily accept it. There's now a test that forges an unsigned `alg=none` token
and asserts it's rejected, plus tests for an expired token, a token signed with the
wrong key, and assorted malformed input.

---

## Issue 25 — Login token readable by injected scripts

✅ **Fixed** (where the deployment allows it — read on, the nuance matters)

**What's wrong.** The login token was kept in browser session storage, which any
script running on the page can read.

**The complication.** The obvious fix — an httpOnly cookie, unreadable by scripts —
only works when the frontend and backend share an origin. On a cross-origin
deployment the cookie becomes a *third-party* cookie, which Safari already blocks
and Chrome is phasing out. Applying cookies naively would have **broken login
entirely** for the GitHub Pages demo.

**What made it workable.** The recommended deployment is already same-origin:
Layout A in `deploy/nginx.conf.example` has nginx serve the built frontend *and*
proxy `/auth`, `/chat`, … to the backend on the same host. So the secure path is
available exactly where it matters — the real internal deployment — and only the
public demo is the exception.

**What was done — dual-mode auth:**

- Login now sets an **httpOnly, SameSite=Lax, Secure** cookie *and* returns the
  token in the body as before.
- The backend accepts either, and **prefers the cookie**, since it's the safer one.
- The frontend **probes which mode it has**: after login it asks the backend to
  renew using the cookie alone. If that works, cookie auth is available and the app
  **never writes a token to browser storage at all** — an injected script has
  nothing to read. If it fails, it falls back to session storage as before.
- `POST /auth/logout` clears the cookie. It's deliberately unauthenticated: signing
  out must work even with an expired token, and it only ever removes a cookie.

**CSRF.** Cookies are sent automatically, so a cookie alone would let another site
act as the signed-in user. `SameSite=Lax` stops browsers attaching it to cross-site
POSTs, and every state-changing endpoint here is a POST.

**Two supporting changes.** The rate limiter now reads the cookie as well, or every
cookie-authenticated user would have shared one IP bucket (undoing Issue 6). And
`COOKIE_SECURE` is configurable, because a `Secure` cookie can't be stored over
plain `http://localhost` during development.

**Honest limitation.** On a cross-origin deployment the token is still in session
storage — that's a browser constraint, not a shortcut. The difference is that the
secure path is now the default wherever it's possible, and the fallback is a
deliberate, documented exception rather than the only behaviour.

---

---

# A later sweep — issues 26 to 30

Found after Python and Node were both available, so all five were reproduced by
running code rather than reading it.

## Issue 26 — A letter "q" anywhere in a question silently dropped the year

🧪 Reproduced · ✅ **Fixed** · **This is a P1, and it was hiding in plain sight**

**What's wrong.** Before treating a bare year like "2026" as a date range, the
resolver checked whether the question was really about a quarter. That check asked
whether the text contained `"quarter"` — or the letter **`"q"`**.

The letter `q` appears in ordinary words. **`uniq`ue. `q`uery. fre`q`uency.
`q`uantity. e`q`ual.**

**What it caused — reproduced against the real code:**

| Question | Year resolved? |
|---|---|
| "How many facilities were registered in 2026?" | ✅ 2026 |
| "How many **uniq**ue facilities were registered in 2026?" | ❌ **dropped** |
| "Show me the fre**q**uency of ABHA creation in 2026" | ❌ **dropped** |
| "Run a **q**uery for facilities in 2026" | ❌ **dropped** |
| "What **q**uantity of records were linked in 2026?" | ❌ **dropped** |

Five of seven realistic questions lost their year. And "unique" is not an unusual
word here — `COUNT(DISTINCT ...)` is the house style for counting facilities, so
"how many unique facilities…" is exactly how someone would phrase it.

When the year is dropped, the resolved-context block handed to the model omits the
period entirely — even though the prompt tells the model to *use those values
directly* — and the period chip disappears from the UI. The model may recover from
the raw question, or may answer over all time and present it as the year's figure.

**What was done.** The check now looks for the actual word "quarter" or `q1`–`q4`,
not a bare letter. Six parametrised tests cover the words that used to break it,
plus one asserting a real quarter reference still wins over a bare year.

---

## Issue 27 — An explicit date was misread as a financial year

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** The financial-year pattern matched *inside* an ISO date. Indian
financial years are written `2025-26`, and `2026-03-15` starts with the same shape.

**What it caused — reproduced:**

| Question | Resolved period | Correct? |
|---|---|---|
| "ABHA created on **2026-03-15**" | FY2026-27 → Apr 2026 – Apr 2027 | ❌ **doesn't even contain 15 March 2026** |
| "…between **2026-04-01 and 2026-06-30**" | FY2025-26 → the whole year | ❌ far broader than asked |

The first is the worse one: the resolved range *excludes the very date asked
about*, and it is injected into the prompt and shown to the user as a confident
chip. A user asking about one day could be shown a full-year total.

**What was done.** Explicit dates are now recognised **first**, before the
financial-year patterns, and resolve to exactly what was asked:

- a single date → that one day (end-exclusive, so the next day)
- a range → precisely that range, inclusive of the end date the user named
  (`…and 2026-06-30` → up to but not including 1 July)

Six joiners are supported (`to`, `and`, `till`, `until`, `through`, `-`), an
impossible date like `2026-13-45` falls through instead of raising, and a reversed
range is ignored rather than producing `start > end`. The financial-year pattern
also gained a guard so it can't match a third date component. `2025-26` still
resolves as a financial year — there's a test for that too.

---

## Issue 28 — Tests never ran in CI

✅ **Fixed**

**What's wrong.** The only workflow built and deployed the frontend. **No job ran
the backend's tests**, and the frontend job ran `npm run build` but never `npm
test`.

**What it caused.** 170 backend tests and 36 frontend tests that existed but were
never enforced. Tests that don't run in CI stop being trusted, and then stop being
true.

**What was done.** A `Tests` workflow runs both suites on every push and pull
request, with the two environment flags the backend tests need. It also runs an
advisory production-dependency audit.

---

## Issue 29 — 14 dependency vulnerabilities, one critical

🧪 Reproduced (`npm audit`) · ✅ **10 of 14 fixed; the other 4 are unreachable here**

**What's wrong.** The frontend had **14 known vulnerabilities: 1 critical, 7 high,
6 moderate.** Nothing had ever scanned for them.

**What was done.** Non-breaking fixes applied, then the dev toolchain upgraded
(`vite` 6 → 8, `vitest` 2 → 5, `@vitejs/plugin-react` 4 → 6). Verified after: build
succeeds, typecheck passes, all 36 tests pass.

**14 → 4. Critical: 1 → 0. High: 7 → 2.** The build also got *faster* (6.2 s →
1.0 s) and slightly *smaller* (60.13 → 58.92 KB gzipped).

**The remaining 4, and why downgrading would be worse:**

| Package | Advisory | Why it isn't reachable |
|---|---|---|
| `image-size` (high, via `pptxgenjs`) | Denial of service via infinite loops in the **JXL, HEIF and ICNS image parsers** | The app passes **no images** to pptxgenjs — exports are native chart objects and text, verified by search. Those parsers are never invoked. The only offered "fix" is pptxgenjs@4.0.0, a *downgrade* from the installed 4.0.1, which is also the latest published version — so no forward fix exists |
| `uuid` (moderate, via `exceljs`) | Missing buffer bounds check **when `buf` is provided** | exceljs never passes `buf`. The only fix is downgrading exceljs 4 → 3, which would break the Excel export — a real feature users rely on |

Both also run **client-side, in the user's own browser, on their own data** — there
is no attacker-supplied input path. Documented rather than "fixed" by breaking two
export features.

---

## Issue 30 — Replacing a PDF served a stale page image

📖 Read from code · ✅ **Fixed**

**What's wrong.** Rendered page images were cached on `(document id, page, dpi)`.
For the local folder source the id is derived from the filename, so replacing a PDF
with an updated version of the same name reuses the id — and the cache kept serving
the **old** image.

**What it caused.** The search index *does* invalidate by file fingerprint, so after
a replacement the citation boxes are recomputed from the new text while the page
picture is still the old one. The highlight then sits over unrelated words — the
feature's one job is pointing at the right line, so this quietly undermines exactly
what it's for. Narrow (needs a live replacement) but wrong when it happens.

**What was done.** The file's fingerprint is now part of the cache key, so the
renderer invalidates on the same signal the index already uses.

---

---

# A third sweep — issues 31 to 35

Covering the areas not yet read line by line: the OCR module (the largest single
file) and the remaining frontend components. All five were reproduced by running
code.

## Issue 31 — Column totals vanished on core metrics

🧪 Reproduced · ✅ **Fixed** · **P1**

**What's wrong.** Result tables show a total per column, but only for columns that
are genuinely additive — summing LGD codes or averages is meaningless. The check
for "is this summable?" tested whether the column name **contained** certain
substrings.

Substrings hide inside ordinary words:

| Column | Total | Why |
|---|---|---|
| `registrations` | ❌ suppressed | "regist**ratio**ns" contains `ratio` |
| `registration_count` | ❌ suppressed | same |
| `population` | ❌ suppressed | "popu**lat**ion" contains `lat` |
| `cumulative_total` | ❌ suppressed | "cumu**lat**ive" contains `lat` |
| `related_facilities` | ❌ suppressed | "re**lat**ed" contains `lat` |
| `operations` | ❌ suppressed | "ope**ratio**ns" contains `ratio` |
| `scan_and_share` | ❌ suppressed | `share` matched |

**What it caused.** **Facility and professional *registration*, and *Scan & Share*
transactions, are headline metrics of this tool** — and their column totals
silently disappeared. The model picks its own column aliases, so whether a user
saw a total depended on whether the alias happened to contain a hidden substring.
Nothing errored; the total just wasn't there.

**What was done.** The check now splits the column name into words and matches
whole words, so `registrations` is one word and no longer contains `ratio`.
`share` was dropped from the list entirely — in this dataset it almost always
means Scan & Share, which is a count; a genuine proportion is still caught by
`pct`/`percent`. 54 tests cover both directions.

---

## Issue 32 — Rates and averages were being summed

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** The same check was wrong in the *opposite* direction too. It
used word-boundary markers for `avg` and `rate` — but **in JavaScript a word
boundary does not break at an underscore**, because `_` counts as a word
character.

**What it caused.** `avg_amount`, `success_rate` and `paid_rate` never matched, so
the app **added up averages and rates** and presented the result as a total. A
summed success rate is not a number that means anything, but it looked like one.

**What was done.** Fixed by the same word-splitting change as #31 — splitting on
underscores makes `avg` and `rate` their own words, so they now match.

> Worth noting how these two travelled together: one regex was simultaneously too
> loose (silently dropping real totals) and too strict (silently summing rates).
> Both directions are covered by tests now.

---

## Issue 33 — A tall word merged two OCR lines into one

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** The Google Vision OCR path groups word boxes into lines by
checking whether a word's vertical centre falls inside a line's span — and it
**grew that span with every word added**.

**What it caused.** One unusually tall element — a superscript, a table rule, an
inline mark — stretched its line's span until the *next* line's centre fell inside
it, merging two lines into one. A citation highlight then covered text the answer
never cited. Since pointing at the exact cited line is the entire purpose of the
feature, this quietly undermined what it exists to do.

Reproduced with synthetic word boxes: two clearly separate lines with one tall
word between them collapsed into a single line.

**What was done.** Words are now matched against the line's **anchor** — the
centre of its first word — rather than an accumulating span, with a tolerance
taken from the smaller of the two word heights so a tall word cannot widen the
band for everything after it. This is the same approach the text-layer path in
`ingest.py` already used, so the two engines now agree.

14 new tests cover both engines: grouping, reading order, page-fraction
normalisation, low-confidence filtering, API errors, and zero-size pages.

---

## Issue 34 — PDF page images leaked memory

📖 Read from code · ✅ **Fixed**

**What's wrong.** Two problems in the PDF viewer:

1. When a page fetch was **superseded** — you page forward before the previous
   image arrives — the code dropped the reference without releasing the image. The
   browser holds it for the life of the tab.
2. The page cache was **unbounded**: one image per page visited, released only when
   the component unmounted.

**What it caused.** Paging quickly through a document leaked a few hundred KB per
skipped page, and browsing a long document held every page visited in memory at
once.

**What was done.** Superseded images are now released explicitly, and the cache is
bounded to a small working set — enough to page back and forth without refetching
— evicting oldest first.

---

## Issue 35 — Dead parameter in the OCR entry point

🧪 Reproduced · ✅ **Fixed**

`ocr_document()` declared a `page_dims` parameter, and the caller dutifully built
and passed it, but **nothing ever read it** — confirmed by search. Harmless, but
it implies the OCR path needs the caller's page dimensions when it doesn't (boxes
are measured against the rendered image and returned as fractions). Removed, along
with the list the caller was building for it.

---

---

# A fourth sweep — issues 36 to 41

Covering the last unread modules: the geography resolver's resolution logic, the
chart decision engine, and the export helpers.

## Issue 36 — 55 district names answered about the wrong district

🧪 Reproduced · ✅ **Fixed** · **P1 — the most consequential finding in this document after #2**

**What's wrong.** When a place name matched more than one district, the resolver
decided it was ambiguous only if the candidates spanned **more than one state**.
But **a post-2011 district split leaves parent and child in the same state**, so
that check never fired for them — and whichever entry happened to be indexed
first won.

**What it caused — reproduced against the real reference workbook:**

| User asks about | System silently answered about |
|---|---|
| **Udaipur** | Salumbar |
| **Barmer** | Balotra |
| **Nagaur** | Didwana-Kuchaman |
| **Sultanpur** | Amethi |
| **Sangrur** | Malerkotla |
| **Ferozepur** | Fazilka |

**95 district names collide within a single state; 55 of them resolved to a
different district than the one named.** These are not obscure places — Udaipur,
Barmer and Sultanpur are major districts. The wrong LGD code went into the
prompt, the wrong name went on the context chip, and the answer was about
somewhere else. No error, no clarifying question.

The sharpest irony: handling post-2011 splits is called out explicitly as a
responsibility of this layer in the original design — and this is precisely that
case, handled backwards.

**What was done.** Ambiguity is now decided on **distinct districts**, not
distinct states. And when several districts answer to one name, if exactly one of
them actually *carries* that name and they are all in the same state, that one
wins — the others are pre-split aliases of it. So "Udaipur" means Udaipur, while
genuinely undecidable names still ask:

- Hamirpur (Himachal **and** Uttar Pradesh) → still asks
- Aurangabad (Bihar, **and** Maharashtra's renamed Chhatrapati Sambhajinagar) → still asks
- Bilaspur, Bijapur, Balrampur → still ask

Six parametrised regression tests pin the split cases, plus tests that cross-state
ambiguity survives and that a renamed district still resolves via its old name.

---

## Issue 37 — Place detection re-resolved by the wrong name

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** When scanning a question for place names, the code found the
matching district entries and then **threw them away** — taking the first entry's
canonical name and looking *that* up again.

**What it caused.** If the text matched an **alias**, the second lookup resolved
the alias's sibling instead. Text saying "Ferozepur" found entries whose first
was Fazilka, then confirmed Fazilka by name. Compounding #36, and wrong on its
own: 48 alias keys re-resolved to something other than what matched.

**What was done.** Detection now resolves the entries it actually matched,
through the same shared helper as direct lookup, so the two paths cannot diverge.

---

## Issue 38 — Grouped charts dropped categories silently

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** A chart caps how many categories it plots, or the bars become
unreadable. The single-series path handles this properly: it keeps the top ones
and folds the remainder into an **"Other"** bar, then prints *"Top 13 shown; the
rest grouped as Other."*

The **grouped** (pivot) path just **cut the list off** — no "Other", nothing
carried over. And the chart component explicitly suppressed the explanatory note
for grouped charts.

**What it caused.** A grouped chart over more than 14 categories — say ABHA by
district split by ownership across 30 districts — showed 14 of them, omitted the
rest entirely, and **said nothing**. The chart looked complete. The bars didn't
sum to the real total.

**What was done.** The grouped path now folds its overflow into "Other", summing
each group, and the note is shown for grouped charts too. Six tests cover it,
including one asserting **the grand total per group is preserved** — that the
plotted numbers still add up to the real ones.

---

## Issue 39 — An internal sort key leaked into chart rows

✅ **Fixed.** Rows were ordered using a temporary `__t` total that was left
attached to the data handed to the chart library. Nothing consumed it today, but
it sat one careless `Object.keys()` away from appearing in a tooltip or an export.
Stripped before returning, with a test on both paths.

---

## Issue 40 — Excel export: merge stopped at column Z; widths ignored labels

✅ **Fixed** · 🧪 *(and one suspicion disproved by testing)*

Two small defects in the spreadsheet export:

1. **Column letters were computed as `String.fromCharCode(64 + n)`**, clamped at
   26. A result with more than 26 columns had its title and footnote spanning only
   the first 26; zero columns produced the nonsense range `A1:@1`. Replaced with
   proper letters (27 → `AA`).
2. **Column widths were measured from the stored value, not the displayed one.**
   The sheet shows "Government" but the width was computed from the stored `"G"`,
   so coded columns came out too narrow to read.

> **A suspicion I checked and dropped.** I expected `A1:@1` and a 1×1 merge to
> make the export *throw* — a single-column result like `SELECT COUNT(*) AS n` is
> very common, so that would have been serious. I tested all five cases against
> the real library: **every one succeeded.** Cosmetic only. Worth recording,
> because reporting it as a crash would have been wrong.

---

## Issue 41 — Two dozen districts filed under an empty name

✅ **Fixed.** Alias columns in the reference workbook sometimes hold only
punctuation, which normalised to an empty string — collecting 24 unrelated
districts under a single empty key. Both lookup paths happened to guard against
an empty key, so nothing broke, but it was junk sitting in the index waiting for
a future caller to trip over. Filtered at load, with a test.

---

---

# A fifth sweep — issues 42 to 45

The last unread components and the build configuration.

## Issue 42 — Tables showed raw codes while charts showed names

✅ **Fixed**

**What's wrong.** The dataset stores coded values — ownership is `G`/`P`/`PP`,
`active` is `t`/`f`, professional type is `d`/`n`/`p`. The chart decodes these
into "Government", "Private", "Doctor". **The result table did not.**

**What it caused.** The same answer showed `G` in the table and "Government" in
the chart beside it. Officials reading the table saw codes with no legend —
exactly the values `GOVERNANCE.md` warns are not self-explanatory.

**What was done.** The table now decodes through the same mapping the charts use,
so the two agree.

---

## Issue 43 — The two Excel buttons produced different files

✅ **Fixed**

**What's wrong.** Both the table and the chart offer an Excel download. The chart's
button passed a label formatter; **the table's did not.**

**What it caused.** Two different spreadsheets from identical data depending on
which button you pressed — one with "Government", one with `G`. Since these files
get emailed onward, two versions of the same week's figures could circulate.

**What was done.** Both paths now pass the same formatter. A related sizing bug
went with it: column widths were computed from the *stored* value, so a column
displaying "Government" was sized from the stored `"G"` and came out too narrow.

---

## Issue 44 — Horizontal bar charts exported as vertical columns

✅ **Fixed**

The chart engine deliberately turns bars sideways once there are many categories
or the labels are long — that is what makes a 14-state ranking readable. The
PowerPoint export hardcoded vertical columns, so the slide did not match the
screen and long state names ended up crammed onto the category axis. The
orientation is now passed through.

---

## Issue 45 — Dead-code checks were switched off, hiding real dead code

🧪 Reproduced · ✅ **Fixed**

**What's wrong.** `tsconfig.json` explicitly set `noUnusedLocals: false` and
`noUnusedParameters: false` — the two compiler checks that catch dead code. (These
are on by default in a standard Vite setup; they had been turned off.)

**What it caused.** Dead code accumulated silently. Turning them on immediately
surfaced three real instances:

| Finding | Consequence |
|---|---|
| `AreaChart` imported from recharts, never used | Dead weight in the chart bundle |
| `xIsTime` destructured, never read | Noise |
| `formatTotal` imported into the Excel helper, never used | Noise |

Following the first one up: `renderSeries` is only ever called with `"line"` and
`"bar"`, because `allowedTypes()` never offers "area" and `defaultType()` folds a
requested area chart into a line. So **the entire area-chart branch was
unreachable** — it existed only to pull recharts' area modules into the build.

**What was done.** All three removed, the unreachable branch deleted, and the
checks left **on** so this cannot silently accumulate again. The build passes
with them enabled.

**Measured result:** the chart chunk went from **438.02 KB (125.75 KB gzipped) to
408.15 KB (115.63 KB gzipped)** — 10 KB off the download, for deleting code that
could never run.

> One thing to note honestly: the main bundle grew by 1 KB, because the result
> table now imports the label mapping from the chart engine (Issue 42). The chart
> engine has no charting-library dependency, so this does not drag recharts into
> the initial download — verified by the chart still building as a separate chunk.

---

# Also noted, deliberately not changed

| Observation | Why it was left alone |
|---|---|
| The weekly deck defines its own `G`/`P`/`PP` and `d`/`n`/`p` decoders, duplicating the chart engine's | Both are correct today. Consolidating means refactoring a module with 98 tests behind it for a maintainability gain, so instead **both sites now carry a comment pointing at the other**, so whoever adds a new code sees it |
| The report's date window (`2026-01-01`–`2026-07-10`) is hardcoded in the frontend and differs from the backend's `2024-07-01`–`2026-07-11` | Not a bug: most tables start Jan 2026 and only Scan & Pay reaches back to 2024, so the narrower picker is right *for a weekly report across all tables*. But it is a constant duplicated across the stack. The clean fix is a small endpoint exposing the window; that is a feature, not a cleanup |
| `MessageBubble` is not memoised, so every past answer re-renders when a new one arrives | The expensive part — totals — is now memoised (Issue 42's change). Memoising the component needs the parent's callbacks wrapped too, and the remaining cost is ordinary React reconciliation of at most 100 table rows. Not worth the risk without a measurement showing it matters |

---

# What remains open

**Nothing.** Every issue in this document is fixed.

Two things are worth carrying forward as *awareness* rather than open work:

| Item | Note |
|---|---|
| Cross-origin deployments still store the token | A browser limitation (third-party cookies). Same-origin deployment — the recommended layout — avoids it entirely |
| PDF index still loads per server process | Re-reads a cache file; does **not** re-embed, so no AI cost. Removing it means building the index as a deploy step |

---

# How these findings were verified

Environment: Python 3.14.7, with the same SQL parser version the project pins, in a
fresh virtual environment.

**Baseline first:** the existing suite was run before any changes — **25 passed**. So
every finding is against a codebase whose own tests were green. After the fixes:
**54 passed**.

| Finding | How it was checked | Result |
|---|---|---|
| 2 — star bypass | Ran both checks over star queries vs explicit-column controls | ✅ Star allowed where the explicit column is blocked |
| 2 — the `COUNT(*)` trap | Applied the naive fix to 18 legitimate queries from the tests and the report | ✅ Would wrongly reject 11 of 18 |
| 2 — the shipped fix | Tested against 7 dangerous and 18 legitimate queries | ✅ 7/7 rejected, 18/18 accepted |
| 3 — role check fails open | Passed three unparseable strings as a `viewer` | ✅ All allowed |
| 5 — session hijack | Made a session as "alice", requested it as "bob" | ✅ Bob got alice's session and history |
| 10 — connection leak | Used the connection after its cleanup block | ✅ Confirmed leak; fix verified |
| 11 — no row cap | Validated a query with no row limit | ✅ Passed unchanged |
| 12 — timezone prerequisite | Requested the India timezone on this machine | ✅ Failed — the extra package is required |
| 13 — install failure | Ran the full install on Python 3.14 | ✅ Failed before, clean after |
| 15 — dead code | Searched for callers | ✅ None |
| 4 — boot guard | Ran the guard with the escape hatch on and off | ✅ Refuses when off, allows when on |
| 6 — rate-limit key | Called the key function with and without a token | ✅ `user:analyst` vs address fallback |
| 12 — IST clock | Compared the two clocks | ✅ +5:30 offset confirmed |

Issues 1, 7, 8, 14, 16, 17 and parts of 18 were traced through the source but not
executed, because they depend on deployment configuration, request timing, or live
cloud credentials rather than on logic that can be tested locally. Each names the file
where the behaviour originates so it can be confirmed on a deployed instance.
