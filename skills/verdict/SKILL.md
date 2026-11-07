---
name: verdict
description: >-
  Run a dated, evidence-graded verdict on a project as a technical lever, product, and business
  against the strongest alternatives—backed by a deterministic epistemic checker, campus-network
  academic database search (ScienceDirect, IEEE, ACM, Springer, arXiv), longitudinal JSONL ledger,
  and offline HTML report. Use when asked whether a project is promising, fundable, or competitive;
  after a new benchmark, simulation, pilot, or field result; after a feature ships or is dropped;
  before grant applications or investor talks; or when searching primary literature for a verdict.
---

# Verdict

A verdict is only as strong as the evidence behind it. This skill pairs agent research and
judgement with a deterministic CLI ([scripts/verdict](./scripts/verdict)) that enforces evidence
ceilings, searches academic databases directly from the user's machine/campus IP, checks review
provenance, tracks kill criteria, records structured runs to `ledger.jsonl`, and compiles
`report.html`.

- Schema & error codes: [references/schema.md](./references/schema.md)
- University network & ScienceDirect / IEEE / ACM / Springer setup:
  [references/academic-search.md](./references/academic-search.md)

## 1. Run deterministic recon (`verdict diff`)

`diff`, `check`, `record`, `render`, and `report` run **100% offline** with no network calls:

```bash
./scripts/verdict diff
```

If no `task.json` exists yet, initialize one with a domain preset (`general`, `systems`, `ml`,
`saas`, or `research`):

```bash
./scripts/verdict init private/verdicts --name "<Project>" --preset systems --with-findings
```

Read, in this order:

1. Previous verdicts in `<verdicts_dir>/` (newest first) and the movement summary from `verdict diff`.
2. Review log (`watched_paths.reviews`, if present): open findings, findings added since the last
   verdict, and which claims an independent human outside the project has checked. AI reviews count
   as `E0` (`self-assessed`), never `reviewed`.
3. Every new result, ADR, or roadmap change flagged by `verdict diff`.
4. Project business, market, funding, and pipeline documents.

## 2. Refresh literature and market evidence

### A. Academic & publisher databases over the user's network (`verdict search` / `verdict fetch`)

Built-in cloud web-search tools run from remote servers and **cannot** use a user's university IP or
campus VPN subscription. When checking scientific literature, measured benchmarks, or negative
results, run `./scripts/verdict search` and `./scripts/verdict fetch` locally (requesting unsandboxed
network access if running inside a network-isolated sandbox so traffic exits via the user's campus IP):

```bash
# Check campus connectivity & ScienceDirect IP entitlement
./scripts/verdict netcheck

# Search ScienceDirect, IEEE, ACM, Springer, and arXiv since 2024
./scripts/verdict search "dynamic power management cluster scheduling" \
  --db sciencedirect,ieee,acm,springer,arxiv --since-year 2024

# Fetch full text or verified PDF (uses ScienceDirect campus entitlement + Springer/ACM/OA PDFs)
./scripts/verdict fetch 10.1016/j.future.2024.01.001
```

Read primary sources (extracted `.txt`/`.pdf`), never second-hand summaries alone.

### B. Competitors, open-source baselines, and funding terms

Search the web for:
- Each tracked competitor flagged stale by `verdict diff`, plus new entrants: funding, deployment
  model, core lever, and whether claims are measured vs. estimated.
- Open-source alternatives (`baselines` in `task.json`).
- Grant and investor terms the roadmap depends on in the next 90 days.

**Privacy rule:** never include partner names, unpublished numbers, local paths, or private pricing
in outbound search queries. Unchecked general knowledge must carry `"note": "known, not re-checked"`.

## 3. Grade evidence and judge each dimension

Follow `evidence_ladder` in `task.json` (default `E0` desk/AI → `E1` assumed model → `E2` replay on
measured public data → `E3` controlled pilot / pre-registered experiment → `E4` partner holdout →
`E5` renewed paying customer):

- **Confidence ceiling:** below `causal_floor` (`E3`), effects are `"predicted"`, never `"saves"`,
  `"reduces"`, `"cuts"`, or `"improves"`. `verdict check` rejects causal verbs below the floor.
- **Options vs. strengths:** unbuilt features or claims below `strength_floor` (`E2`) without a
  structural basis belong in `options`, never `strengths`.
- **Strongest alternative:** compare against the strongest free or commercial baseline (`B1`,
  incumbents, or employment for founder time), never a strawman (`B0` alone).
- **Disconfirming first:** list disconfirming results, failed checks, and dropped features before
  confirming ones. Log items killed by pre-stated rules in `killed_or_failed`.
- **Structural vs. feature-dependent:** separate constraints no feature can move from levers
  engineering can change.
- **Provenance:** mark every track `self-assessed` unless an independent human outside the project
  confirmed it (`reviewed` with a human-verified finding reference).

## 4. Validate, record, and render (`verdict record` & `verdict report`)

1. Write the candidate structured entry JSON conforming to [references/schema.md](./references/schema.md),
   setting `head_commit` to `git rev-parse --short HEAD`.
2. Validate and record (aborts automatically on any `ERROR`):
   ```bash
   ./scripts/verdict record /tmp/verdict-YYYY-MM-DD.json --write-md
   ./scripts/verdict check
   ./scripts/verdict report
   ```
   *(Keep private business verdicts in a gitignored directory.)*

## 5. Report in chat

Give a concise summary:
- One-line verdicts with evidence levels and movements since the last verdict.
- Outside-human-reviewed claim count (`0` if none).
- Overdue or upcoming kill-criteria dates.
- The **next cheapest test** (highest verdict-shift per hour spent).
- Links to `<verdicts_dir>/YYYY-MM-DD.md` and `<verdicts_dir>/report.html`.
- If the verdict contradicts the roadmap or business plan, name the conflict and propose the edit—do
  not apply it without asking.

