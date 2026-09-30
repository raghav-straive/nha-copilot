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
| 9 | Explorer and PDF chat don't reach the audit log | P1 | ✅ Fixed (Explorer) |
| 10 | Database connections are never closed | P1 | ✅ Fixed |
| 11 | Nothing caps how many rows come back | P1 | ✅ Fixed |
| 12 | "Today" is the server's date, not India's | P1 | ✅ Fixed |
| 13 | Dependencies don't install on the newest Python | P2 | ✅ Fixed |
| 14 | The architecture doc describes a system that no longer exists | P2 | ⬜ Open |
| 15 | Dead code | P2 | ✅ Fixed |
| 16 | The PDF cache can be corrupted by a crash | P2 | ✅ Fixed |
| 17 | Test coverage stops at the oldest modules | P2 | ⬜ Partly |
| 18 | Six smaller items | P2 | Mixed |

**Tests: 25 → 54.** Every security finding above is now covered by a test.

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

> **Still slightly imperfect:** the Explorer cache and the PDF index are *still*
> built once per process. That's duplicated work on a cold start, not wrong
> behaviour, and it's noted in a comment in the deployment file.

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

**What was done.** Explorer now logs every question it runs, deliberately **before**
the success filter so failures are captured too. Logging is wrapped so it can never
break generation.

⬜ **Still open:** PDF chat questions are still unlogged, and log rows don't yet carry
a column saying which feature produced them. Both are small additions.

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

⬜ **Open — deliberately left alone**

`docs/architecture.md` carries a clear "SUPERSEDED" banner, which is good. But it's
still 700 lines describing a different database, different tables, and clinical codes
that are no longer part of this project. A newcomer — or an AI assistant pointed at
the repo — will absorb the wrong data model.

**Why it's still open.** Trimming it means deciding which design reasoning is worth
keeping, which is an editorial judgement rather than a mechanical fix. The suggestion
is to keep the design-principles and "why free-form SQL" sections and move the rest to
a `history` folder.

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

⬜ **Partly addressed**

The suite originally covered the validator, the role check, the geography layer, and
the pipeline; the frontend covered the chart engine. **Nothing covered the weekly
report, the Explorer, or any of the PDF chat** — where the newest and most expensive
logic lives.

Worth sitting with: **a three-line test asserting that a `SELECT *` query is denied to
a viewer would have caught Issue 2**, the most serious finding in this review.

**What was done.** Tests went from 25 to 54, covering every security finding: star
projections (including subquery, multi-part and qualified forms), `COUNT(*)`
regression guards, the row cap, the role check failing closed, and session ownership
and persistence.

⬜ **Still open:** the weekly report, the Explorer, and PDF chat remain untested. The
highest-value additions would be the citation-renumbering logic in PDF chat and the
number-formatting helpers in the weekly report.

---

## Issue 18 — Smaller items

| Item | Where | Status |
|---|---|---|
| Unbounded log query — asking for a huge number of log rows scanned the whole table | `query_log/router.py` | ✅ Fixed (clamped) |
| Shallow health check — reported "ok" without checking anything, so a monitor showed green while every query failed | `main.py` | ✅ Fixed (now reports what actually loaded) |
| Deprecated startup style — the app-startup mechanism used here is deprecated in the current web framework | `main.py:51` | ⬜ Open — works today, will need migrating |
| Password truncation — passwords are cut at 72 bytes, which can slice a multi-byte character in half | `auth/users.py:22` | ⬜ Open — harmless today, latent oddity |
| Login token in browser storage — readable by any injected script | `frontend/src/App.tsx:14` | ⬜ Open — acceptable prototype trade-off, worth stating in the security notes |
| 8-hour token with no renewal — a long analyst session dies mid-work | `config.py:57` | ⬜ Open — needs a refresh mechanism, which is a feature not a fix |

---

# What remains open

Nothing here is urgent; all are judgement calls or additions rather than defects.

| # | Item | Why it's still open |
|---|---|---|
| 14 | Trim the superseded architecture doc | Editorial call on what reasoning to keep |
| 9 | Log PDF chat questions; tag log rows by feature | Small addition, needs a schema column |
| 17 | Tests for report / Explorer / PDF chat | Worth doing, none of it is load-bearing for the security fixes |
| 18 | Deprecated startup style | Works today; migrate when convenient |
| 18 | Password byte truncation | Harmless in practice |
| 18 | Login token storage | Accepted prototype trade-off |
| 18 | Token renewal | A feature, not a fix |
| — | Explorer / PDF caches still per-process | Duplicate cold-start cost, not incorrect |

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
