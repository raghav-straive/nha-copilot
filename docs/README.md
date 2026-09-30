# Documentation map

Start here. The authoritative documents are listed first; anything under
`history/` describes a design the project has since moved away from and should
not be used as a reference.

## Current

| Document | What it covers |
|---|---|
| [`../README.md`](../README.md) | Setup, prerequisites, endpoints, how to run the tests |
| [`../backend/GOVERNANCE.md`](../backend/GOVERNANCE.md) | **The authoritative data model.** Table schemas, join rules, coded values, business definitions. Loaded verbatim as the LLM's system prompt, so it is both documentation and executable configuration |
| [`../brief.md`](../brief.md) | What the project does and how it works, end to end, in plain language |
| [`../issues.md`](../issues.md) | Every issue found in review, with fix status |
| [`../optimizations.md`](../optimizations.md) | Speed and cost improvements, with status |
| [`../deploy/SELF_HOSTING.md`](../deploy/SELF_HOSTING.md) | Step-by-step self-hosting guide (nginx / IIS / Docker) |
| [`../deploy/README.md`](../deploy/README.md) | Reference GCP VM + GitHub Pages setup |

## History — do not use as a reference

| Document | Why it's kept |
|---|---|
| [`history/architecture-pmjay-superseded.md`](history/architecture-pmjay-superseded.md) | The original **PM-JAY claims (TMS/BIS)** design, on PostgreSQL with synthetic data. The project has since moved to the **ABDM digital-adoption** domain: nine BigQuery tables, no merged table. The architectural *shape* still holds — React → FastAPI → BigQuery, three-layer SQL safety, RBAC, NL-to-SQL from a governance prompt — but **every table, column and domain rule in it is out of date.** Kept for design rationale only: sections 2 (design principles) and 4.3 (why free-form SQL generation over a parameterised function library) explain decisions the current system still rests on |

> **Why this file exists.** The superseded document is 700 lines and carries a
> banner, but a banner is easy to scroll past — and a newcomer or an AI assistant
> pointed at `docs/` would happily absorb the wrong data model. Moving it under
> `history/` makes the status structural rather than something you have to read
> to discover.
