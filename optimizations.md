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

| # | Optimization | Payoff | Effort | Status |
|---|---|---|---|---|
| 1 | Run the weekly report's queries in parallel | **20–40 s → a few seconds** | Half a day | ✅ Done |
| 2 | Cache the weekly report per date range | Repeat views instant | Small | ✅ Done |
| 3 | Make the PDF cache write crash-proof | Avoids a full rebuild | Tiny | ✅ Done |
| 4 | Delete dead code | Clarity | Tiny | ✅ Done |
| 5 | Keep the prompt cache-friendly | Already saving money | Comment only | ✅ Done |
| 6 | Speed up PDF search with a numerical library | Only matters as the corpus grows | Small | ⬜ Available |
| 7 | Store embeddings in binary rather than text | ~10× smaller cache | Small | ⬜ Available |
| 8 | Use the "dry run" for better error messages | Clearer failures | Small | ⬜ Available |
| 9 | Cache repeated questions | Cuts AI + database cost | Medium | ⬜ Available |
| 10 | Load the charting library on demand | ~500 KB off first load | Small | ⬜ Measure first |
| 11 | Rewrite non-English column labels locally | — | — | ❌ Rejected |

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

# Available

## 6. Speed up PDF search with a numerical library ⬜

**The problem.** Matching a question against stored passages runs as a plain Python
loop over every passage, on every search. Python loops are slow for this kind of
arithmetic — a numerical library does the same work as a single bulk operation, often
tens of times faster.

There's a second saving available: part of the calculation depends only on the stored
passages, not the question, so it can be computed once when the index is built rather
than repeated on every search.

**Why it wasn't done.** At the current corpus size this simply isn't the bottleneck —
the AI call dominates the response time by a wide margin. It also means adding a
dependency the project doesn't currently have.

**When to do it.** When the PDF corpus grows past a few hundred documents, or if you
want the code simpler — the bulk version is actually shorter than the loop.

## 7. Store embeddings in binary rather than text ⬜

The numerical fingerprints of each passage are currently saved as text numbers, which
takes roughly **ten times** the space of the equivalent binary format. That's disk
space, slower saves, and slower loads.

**Suggested approach.** Keep the descriptive data (which document, which page, the
text) as human-readable text, and move just the numbers to a binary companion file.
Worth doing alongside #6, since both touch the same area.

## 8. Use the "dry run" for better error messages ⬜

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

## 9. Cache repeated questions ⬜

**The opportunity.** The Explorer regenerates the same popular questions, and
officials re-ask the same things across sessions. Each repeat costs a full AI call plus
a database query.

**What it would buy.** Caching answers for a short window would cut both AI and
database cost on the most common path — likely the largest remaining running-cost
saving available.

**Two rules that must be got right:**
1. **Key on the question *and* the user's role** — roles change what a query is allowed
   to return, so a cached `admin` answer must never be served to a `viewer`.
2. **Never cache a relative date range.** "Today" and "this week" go stale, and a
   stale number is worse than a slow one — it's a wrong answer delivered
   confidently.

**Why it wasn't done.** Those two rules are where this goes wrong, and getting them
wrong reintroduces exactly the trust problem the whole app is built to avoid. Worth
doing deliberately rather than as a bolt-on.

## 10. Load the charting library on demand ⬜ *(measure first)*

**Credit where due — most of this is already done.** I checked, and the heavy
libraries are already loaded only when actually needed: the Excel export, the
PowerPoint export, and the PDF viewer. The imports that *look* eager elsewhere only
pull in thin wrapper files, not the libraries themselves. Whoever built this clearly
knew the technique.

**What's left.** The charting library (~500 KB) is still loaded upfront, even though
no chart exists until the first answer arrives. The same treatment the PDF viewer
already gets would defer it.

**Why it wasn't done.** It should be measured first — build the frontend and check the
reported bundle sizes — because it adds a loading boundary for what may be a modest
saving on a tool used by officials on office connections.

---

# Rejected

## 11. Rewriting non-English column labels locally ❌

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

# Measured results

| Change | Before | After |
|---|---|---|
| Weekly report load | ~20–40 s (20 queries in sequence) | A few seconds (concurrent) |
| Weekly report, repeat view | Full rebuild every time | Served from cache for 6 hours |
| Explorer tab, 3 users at once on a cold cache | ~30 AI calls, ~18 database queries | One build serves all three |
| PDF corpus, concurrent cold requests | Each re-processed everything | One build serves all |
| Crash during a PDF cache write | Full corpus re-processed | Previous copy intact |

The Explorer and PDF savings are counted under Issue 8 in [`issues.md`](issues.md) —
they were implemented as correctness fixes (missing locks) but their main effect is
cost.

---

# Not an optimization, but worth knowing

**The Explorer cache and the PDF index are still built once per server process.** With
two processes running, that's two builds instead of one on a cold start.

This is **duplicated cost, not incorrect behaviour** — each process produces the same
result. Chat sessions, which *were* a correctness problem, are now shared via a
database (Issue 1).

**If you want to remove the duplication:** treat the PDF index as a file built once by
a separate command and loaded at startup, rather than built on demand by whichever
process gets the first request. That's a deployment change rather than a code
optimization.
