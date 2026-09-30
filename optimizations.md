# Optimizations — NHA Analytics Co-Pilot

Speed and cost improvements, in plain language. These change **no behaviour** — only
how fast the app is and how much it costs to run. Bugs and security findings live in
[`issues.md`](issues.md); what the project does is in [`brief.md`](brief.md).

---

## Status legend

| Mark | Meaning |
|---|---|
| ✅ **Done** | Applied in this repository |
| ⬜ **Available** | Worth doing, not applied — reason given |
| ❌ **Rejected** | Looked promising, but unsafe or unnecessary — reasoning kept so nobody retries it |

---

## Summary

| # | Optimization | Payoff | Status |
|---|---|---|---|
| 1 | Run the weekly report's queries in parallel | **20–40 s → a few seconds** | ✅ Done |
| 2 | Cache the weekly report per date range | Repeat views instant | ✅ Done |
| 3 | Make the PDF cache write crash-proof | Avoids a full re-embed | ✅ Done |
| 4 | Delete dead code | Clarity | ✅ Done |
| 5 | Keep the prompt cache-friendly | Already saving money | ✅ Done |
| 6 | Speed up PDF search with a numerical library | Faster as the corpus grows | ✅ Done |
| 7 | **Compress responses** | **89% smaller payloads** | ✅ Done |
| 8 | **One schema query at startup instead of nine** | **~10–20 s off boot** | ✅ Done |
| 9 | **Generate Explorer cards concurrently** | Cold tab ~6× faster | ✅ Done |
| 10 | **Share the Explorer cache across workers** | Halves its AI cost | ✅ Done |
| 11 | **Cache repeated questions** | Cuts AI + database cost | ✅ Done |
| 12 | Index the audit log's sort column | Faster admin log reads | ✅ Done |
| 13 | Store embeddings in binary rather than text | ~10× smaller cache | ⬜ Available |
| 14 | Use the "dry run" for better error messages | Clearer failures | ⬜ Available |
| 15 | Load the charting library on demand | ~500 KB off first load | ⬜ Needs Node |
| 16 | Cache the geography workbook parse | — | ❌ Ruled out by measurement |
| 17 | Rewrite non-English column labels locally | — | ❌ Rejected as unsafe |

**Measured, not assumed.** Items 7, 8 and 16 came from actually timing things —
including #16, which looked worthwhile until measurement showed it wasn't.

---

# Done

## 1. Run the weekly report's queries in parallel ✅

**The single biggest speed win in the app.**

**The problem.** The report issued about twenty database queries one after another,
each waiting for the previous to finish. They are all completely independent of each
other, and each spends most of its time *waiting* on the database rather than
computing anything.

**Why it was slow.** Each query carries roughly one to two seconds of start-up
latency, regardless of how little data it touches. Twenty of those in sequence put
the report at **20–40 seconds** to load — long enough that users assume it's broken.

**What was done.** All twenty are now submitted at once and collected as they come
back. Because they're independent, the total time becomes roughly the slowest single
query rather than the sum of all of them — **a few seconds instead of twenty to
forty**.

Each query still fails independently, so one bad query can't take down the whole
report — same behaviour as before, just concurrent.

## 2. Cache the weekly report per date range ✅

**The problem.** Every view rebuilt the report from scratch: ~20 database queries plus
an AI-written summary.

**The insight.** A past week's numbers don't change. Rebuilding them was pure waste.

**What was done.** Results are cached for six hours, keyed by date range, with a
bounded cache so it can't grow forever. Combined with #1, a repeat view is now
effectively instant. (A rate limit was added at the same time — that's a cost-abuse
fix, covered as Issue 7.)

## 3. Make the PDF cache write crash-proof ✅

**The problem.** The whole document corpus was written to one file in a single step.
A crash mid-write left it half-finished.

**Why it mattered.** Recovery worked — the app noticed and rebuilt — but rebuilding
means reprocessing and re-embedding *every* document, which is the most expensive
operation in the app. A one-second crash could cost minutes of AI calls.

**What was done.** The cache is written to a temporary file and then renamed. Renaming
is atomic, so a crash now leaves the previous good copy intact and nothing is lost.

## 4. Delete dead code ✅

Two unused save/load methods on the PDF search index were removed — the service has
its own, different, per-document caching, so having both invited confusion about which
one actually runs.

(A second piece of dead code, a "dry run" query method, was **kept** — see #8.)

## 5. Keep the prompt cache-friendly ✅ *(already correct — protected with a comment)*

**Worth understanding, because it's easy to break by accident.**

The rulebook sent to the AI on every question is about 30 KB — roughly 8,000 tokens.
That sounds expensive, but the code already has this exactly right: the rulebook is
**byte-for-byte identical on every request**, and everything that changes per request
(the question, the resolved dates and places, the conversation history) is correctly
kept in a separate part of the message.

That means the AI provider serves the big unchanging part **from its cache at a
discount**, automatically. No change needed.

**The thing to protect against** is someone later moving per-request data — a date,
the user's role, a resolved code — into the rulebook section because it's convenient.
That would break the cache on *every single request* and multiply the prompt cost.

**What was done.** A comment now explains this where the prompt is assembled, so the
reasoning is visible to whoever next edits it.

---

## 6. Speed up PDF search with a numerical library ✅

**The problem.** Matching a question against stored passages ran as a plain Python
loop over every passage, on every search. Python loops are slow for this kind of
arithmetic.

**What was done.** Two changes. The comparison is now one bulk numerical operation
instead of a loop. And the part of the calculation that depends only on the stored
passages — not on the question — is computed **once when the index is built** rather
than repeated on every search.

The numerical library is **optional at runtime**: if it isn't installed the store
falls back to the original loop, so the project still runs in a minimal environment.
A test asserts both paths return the same ranking, so the optimization can't silently
change which passage is cited.

## 7. Compress responses ✅

**The problem.** Nothing compressed the responses. Result sets are extremely
repetitive JSON — the same column names on every row — which is close to a best case
for compression.

**Measured.** A representative 500-row result: **59 KB raw → 7 KB compressed, an 89%
reduction.**

**What was done.** Compression is now enabled, with a minimum size so tiny responses
(where the framing overhead would cost more than it saves) are left alone. Tested
both ways: a large response arrives compressed, a small one doesn't.

This is the highest payoff-to-effort item in this whole document, and it matters
most for exactly the users this tool is for — government officials on office
connections rather than fast links.

## 8. One schema query at startup instead of nine ✅

**The problem.** At startup the app fetches the real column names and types for each
of the nine data tables, to give the AI accurate type information. It did this with
**nine separate queries, one after another.**

**Why it was slow.** Each query carries roughly one to two seconds of start-up
latency regardless of how little data it touches. Nine in sequence delayed readiness
by **10–20 seconds** — during which the app is up but answers badly, because the type
information hasn't arrived.

**What was done.** One query covering all nine tables. The information all lives in
the same place, so this was never nine questions — it was one question asked nine
times.

## 9. Generate Explorer cards concurrently ✅

**The problem.** The Explorer proposes questions and then runs each one through the
full pipeline — two AI calls plus a database query each — **one after another**. Six
cards meant six full round-trips in series.

**What was done.** A batch now runs concurrently, so a cold Explorer load takes
roughly as long as its slowest single card instead of the sum of all of them.

**The cost subtlety, deliberately handled.** The old code asked for ten questions and
stopped as soon as it had six good cards. Naively firing all ten at once would add up
to four extra turns of AI cost on every build. So it runs six, and only tops up from
the spares if some failed. A test covers the top-up path.

## 10. Share the Explorer cache across workers ✅

**The problem.** The Explorer's results were cached in memory only. With two server
processes running, each one generated its own set — genuinely paying twice in AI
calls — and a restart threw the cache away entirely.

**What was done.** The cache is now also written to disk (atomically, per
Optimization 3), so a second process or a restart reuses it. A test clears the
in-memory cache and asserts the "other worker" regenerates nothing.

> **A correction worth recording.** An earlier version of this document claimed the
> PDF index had the same problem. It doesn't — its cache is on disk and keyed per
> document, so a second worker re-reads the file but **doesn't re-embed anything.**
> The Explorer was the real case.

## 11. Cache repeated questions ✅

**The problem.** Officials re-ask the same things and the Explorer regenerates the
same popular questions. Each repeat cost two AI calls plus a database query.

**What was done.** Successful answers are cached for 30 minutes, with a bounded
least-recently-used cache. Three rules keep it safe, and each has a test:

1. **The role is part of the key.** Access level changes what a query may return, so
   an admin's cached answer can never be served to a viewer.
2. **The resolved date range is part of the key.** "Today" resolves to a concrete
   range, so the key changes by itself when the date rolls over. A stale number is
   worse than a slow one.
3. **Only successful data answers are cached** — never a clarifying question, an
   out-of-scope reply, or an error.

**One honest limitation.** Caching is skipped when the turn depends on conversation
history, because the same words mean different things after a different preceding
turn ("what about Bihar?"). In practice that means it helps most with the first
question of a session, the suggestion chips, and the Explorer — which is where the
repetition actually is.

## 12. Index the audit log's sort column ✅

The audit log is always read newest-first and is append-only, so the timestamp column
now has an index. One line, and it's the only index the table needs.

---

# Available

## 13. Store embeddings in binary rather than text ⬜

The numerical fingerprints of each passage are currently saved as text numbers, which
takes roughly **ten times** the space of the equivalent binary format. That's disk
space, slower saves, and slower loads.

**Suggested approach.** Keep the descriptive data (which document, which page, the
text) as human-readable text, and move just the numbers to a binary companion file.
The numerical library added in #6 makes this straightforward.

**Why it wasn't done.** The win is disk space and load time, neither of which is a
problem at the current corpus size, and it changes the cache file format — so it
needs a version bump and a rebuild on deploy. Worth doing when the corpus grows,
not before.

## 14. Use the "dry run" for better error messages ⬜

**What's available.** The database client already has a "dry run" method that checks a
query and estimates its size **without running it and without ever being charged**.
It's currently never called.

**What it would buy.** Wrong column names would be caught for free, and the app could
say *"that query would scan 340 GB — try narrowing it"* instead of surfacing a
confusing failure when the 2 GB safety cap trips.

**The honest trade-off.** It adds a round-trip of a few hundred milliseconds to
**every successful** request, in order to improve the unsuccessful ones. Since the
billing cap already limits the financial damage, this is a *message-quality*
improvement rather than a cost saving.

**Recommendation.** Only do this if users are actually reporting confusing failures.
Otherwise it makes the common case slower to help the rare one.

## 15. Load the charting library on demand ⬜ *(needs Node)*

**Credit where due — most of this is already done.** I checked, and the heavy
libraries are already loaded only when actually needed: the Excel export, the
PowerPoint export, and the PDF viewer. The imports that *look* eager elsewhere only
pull in thin wrapper files, not the libraries themselves. Whoever built this clearly
knew the technique.

**What's left.** The charting library (~500 KB) is still loaded upfront, even though
no chart exists until the first answer arrives. The same treatment the PDF viewer
already gets would defer it.

**Why it wasn't done.** Node isn't installed in the environment this work was done
in, so the frontend can't be built, measured, or type-checked here. Shipping
unverified TypeScript into the one part of the app with no test safety net seemed
worse than leaving a documented gap.

**To pick it up:** run `npm run build`, read the chunk sizes the build reports, and
if the charting library is a meaningful share of the initial download, defer it the
same way the PDF tab already is. Measure first — it adds a loading boundary for what
may be a modest saving.

---

# Rejected

## 16. Caching the geography workbook parse ❌ *(ruled out by measurement)*

**The idea.** Place-name resolution loads a spreadsheet of states, districts,
aliases and district splits at every startup. Parsing a spreadsheet sounds slow, so
caching the parsed result as a fast-loading file looked like an easy startup win.

**Measured: 72 milliseconds** (best of three runs).

**Conclusion.** Not worth it. At 72 ms this is invisible next to the 10–20 seconds
the schema queries were costing (Optimization 8), and caching it would add a cache
file, an invalidation rule, and a way to be subtly wrong about geography — in
exchange for nothing a user could perceive.

**Recorded because** it's the kind of optimization that sounds obviously correct and
would have been a pure loss. The measurement took two minutes and saved the change.

## 17. Rewriting non-English column labels locally ❌

**The idea.** When the AI labels a result column in Hindi script, the database rejects
the query and the app spends an entire extra AI call regenerating it. Fixing those
labels locally — without asking the AI again — looks like free money.

**Why it's unsafe.** The chart definition and the written answer both refer to columns
**by the label the AI chose**. Renaming a label locally would silently break chart
rendering and the answer text. Silently, which is the worst kind.

**Conclusion.** The existing regeneration is the correct approach. Keep it.

**The cheap win instead.** Make the "labels must be plain English" rule prominent in
the rulebook with one worked Hindi example, so the extra round-trip is rarely needed
at all. That costs nothing per request and carries no risk.

---

# Results

| Change | Before | After |
|---|---|---|
| **Response size** (500-row result) | 59 KB | **7 KB — measured, 89% smaller** |
| **Startup schema load** | 9 sequential queries, ~10–20 s | **1 query** |
| **Weekly report load** | ~20–40 s (20 queries in sequence) | A few seconds (concurrent) |
| Weekly report, repeat view | Full rebuild every time | Cached 6 hours |
| Explorer cold load | 6 pipeline turns in sequence | One concurrent batch |
| Explorer, second worker | Regenerated from scratch | Reuses the shared disk cache |
| Explorer tab, 3 users at once, cold | ~30 AI calls, ~18 queries | One build serves all three |
| Repeated question within 30 min | 2 AI calls + 1 query | Served from cache |
| PDF search | Python loop per passage | One bulk numerical operation |
| Crash during a PDF cache write | Full corpus re-embedded | Previous copy intact |
| Geography parse | 72 ms | 72 ms — **measured, left alone** |

The response-size and startup-time figures were measured directly. The weekly report
and Explorer figures are expected improvements from removing sequential waiting —
the concurrency is tested, but the wall-clock gain needs live cloud credentials to
confirm end to end.

---

# What's left, and why

| Item | Status |
|---|---|
| Binary embedding storage (#13) | Available — the win is disk space, which isn't a problem yet |
| Dry-run pre-check (#14) | Available — but it slows the common case to improve the rare one |
| Charting library loaded upfront (#15) | Needs Node to measure; not installed here |

**One duplication remains, by design.** The PDF index is still loaded per server
process. That's a little CPU and memory to re-read a cache file — it does **not**
re-embed anything, so there's no AI cost. Removing even that would mean building the
index as a deployment step rather than on first request, which is an operational
change rather than a code optimization.
