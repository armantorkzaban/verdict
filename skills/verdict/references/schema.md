# Verdict Task & Ledger Schema Reference

## 1. `task.json`

Defines a project's epistemic rules, baselines, strategic tracks, scorecard dimensions, academic
search defaults, and watched workspace paths. Generate a starter file with
`verdict init <dir> --preset general|systems|ml|saas|research`.

| Field | Type | Purpose |
|---|---|---|
| `project` | `string` | Project name shown in CLI and `report.html` |
| `summary` | `string` | One-sentence description of what is being evaluated |
| `workspace_root` | `string` | Relative path from `task.json`'s folder to the repository root |
| `verdicts_dir` | `string` | Directory holding `YYYY-MM-DD.md` and `report.html` (default `"."`) |
| `ledger_file` | `string` | JSONL file holding structured verdict runs (default `"ledger.jsonl"`) |
| `evidence_ladder` | `array` | Ordered `{id, rank, description}` levels (`E0`..`E5`) |
| `rules.causal_floor` | `string` | Minimum evidence ID required before causal verbs (`saves`, `reduces`, `improves`) are permitted |
| `rules.strength_floor` | `string` | Minimum evidence ID required for `strengths` unless `structural: true` |
| `rules.max_source_age_days` | `int` | Days before competitor/external checks trigger `STALE_COMPETITOR_CHECK` |
| `rules.due_soon_days` | `int` | Window before `decision_date` or `kill_date` triggers a horizon warning |
| `literature.default_databases` | `array` | Default scholarly indexes for `verdict search` |
| `literature.ezproxy_prefix` | `string` | Optional university library EZproxy prefix (e.g. `https://ezproxy.uni.edu/login?url=`) |
| `questions` | `array` | `{id, label}` strategic tracks judged in Section 1 |
| `dimensions` | `array` | `{id, label, kind}` (`structural` \| `feature_dependent` \| `mixed`) |
| `watched_paths` | `object` | `{results: [...], decisions: [...], reviews: "...", consistency_targets: [...]}` |

## 2. Ledger Entry (`ledger.jsonl` / `verdict record <entry.json>`)

Each line in `ledger.jsonl` is one self-contained JSON object:

```json
{
  "id": "2026-10-15",
  "date": "2026-10-15",
  "title": "Verdict, 2026-10-15",
  "baseline": false,
  "previous_id": "2026-10-01",
  "head_commit": "a1b2c3d",
  "preamble": "Second verdict after production trace replay...",
  "verdicts": [
    {
      "question_id": "technical_lever",
      "status": "Go with conditions",
      "headline": "Predicted 22% latency drop vs B0; within 2% of tuned B1",
      "summary": "Predicted to lower tail latency against stock defaults, but adds little over a tuned open-source baseline.",
      "evidence": ["E2"],
      "evidence_note": "E2 (two public traces); predicted only",
      "review_status": "self-assessed",
      "review_refs": []
    }
  ],
  "changes": {
    "disconfirming": [
      {
        "text": "Adaptive prefetch heuristic dropped after failing the 5% rule on trace B.",
        "evidence": ["E2"],
        "repo_paths": ["docs/notebook/2026-10-14-prefetch.md"],
        "commits": ["a1b2c3d"]
      }
    ],
    "confirming": [
      {
        "text": "Zero SLO violations across 30-day replay.",
        "evidence": ["E2"],
        "repo_paths": ["docs/notebook/2026-10-14-prefetch.md"]
      }
    ]
  },
  "killed_or_failed": [
    {
      "item": "Adaptive prefetch heuristic",
      "outcome": "dropped",
      "evidence": ["E2"],
      "rule_and_finding": "Gain < 5% over static LRU on trace B (+1.2%)."
    }
  ],
  "scorecard": [
    {
      "dimension_id": "impact_vs_b0",
      "score": "Promising",
      "evidence": ["E2"],
      "change": "Stronger",
      "reasoning": "22% lower p99 latency predicted against default configuration."
    }
  ],
  "outside_review": {
    "claims_checked_by_outside_human": 0,
    "verified_finding_ids": [],
    "open_finding_ids": ["F1"],
    "max_finding_number_seen": 1,
    "summary": "No claims checked by an independent human reviewer yet.",
    "notes": []
  },
  "strengths": [
    {
      "text": "Pre-stated kill rules eliminated a losing feature openly.",
      "evidence": ["E2"],
      "structural": true
    }
  ],
  "options": ["Live shadow-mode proxy", "Partner pilot"],
  "competitors": [
    {
      "name": "AcmeCache",
      "does": "Managed caching proxy",
      "target": "Cloud SaaS",
      "lever": "Static TTL",
      "measured": "Vendor blog only",
      "source": "acmecache.example/docs",
      "checked": "2026-10-15"
    }
  ],
  "risks": [
    {
      "risk": "No design partner grants production shadow access",
      "kill_criterion": "Fewer than 2 partners agree by kill date -> pivot to open diagnostic CLI",
      "decision_date": "2026-11-15",
      "kill_date": "2026-12-31"
    }
  ],
  "next_tests": [
    {
      "title": "Shadow benchmark on partner log export (4 hours)",
      "detail": "Tests E2 -> E3 readiness before building live control plane."
    }
  ],
  "contradictions": [
    {
      "target": "ROADMAP.md",
      "proposal": "Remove adaptive prefetch milestone and gate v0.2 on shadow-mode pilot."
    }
  ],
  "sources": [
    {
      "label": "Prefetch replay notebook",
      "date": "2026-10-14",
      "repo_paths": ["docs/notebook/2026-10-14-prefetch.md"],
      "commit": "a1b2c3d"
    }
  ]
}
```

## 3. Checker Error & Warning Codes (`verdict check`)

- `CAUSAL_BELOW_FLOOR` (`ERROR`): Claim below `causal_floor` uses a causal verb (`saves`, `reduces`, `cuts`, `improves`, `increases`) without hypothetical framing (`predicted`, `would have reduced`) or `"external_claim": true`.
- `STRENGTH_BELOW_FLOOR` (`ERROR`): Item in `strengths` has evidence below `strength_floor` and `structural` is not `true`.
- `AI_REVIEW_MARKED_HUMAN` / `UNVERIFIED_OUTSIDE_REVIEW` (`ERROR`): Claim marked `reviewed` or `claims_checked_by_outside_human > 0` without a human-confirmed finding in the review log.
- `MISSING_DISCONFIRMING` (`ERROR`): Non-baseline verdict lacks disconfirming items and gives no `no_disconfirming_reason`.
- `BROKEN_REPO_PATH` / `BROKEN_COMMIT` (`ERROR`): Cited repository file or git commit does not exist.
- `OVERDUE_DECISION_DATE` / `OVERDUE_KILL_DATE` / `STALE_COMPETITOR_CHECK` (`WARN`, or `ERROR` with `--strict-dates`): Horizon or source-freshness deadline passed relative to `--as-of`.

