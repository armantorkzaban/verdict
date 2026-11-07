"""Project-agnostic domain presets for `verdict init --preset <name>`."""

from __future__ import annotations

import copy
from typing import Any

COMMON_ALLOWED_PHRASES = [
    "saves nothing",
    "saves no ",
    "improves nothing",
    "never \"saves\"",
    "never saves",
    "would have saved",
    "would have reduced",
    "would have improved",
    "measured improvement",
    "measured reduction",
    "measured savings",
    "verified savings",
]

PRESETS: dict[str, dict[str, Any]] = {
    "general": {
        "project": "MyProject",
        "summary": "Evidence-graded evaluation of technical, product, and business viability against the strongest alternatives.",
        "workspace_root": "..",
        "verdicts_dir": ".",
        "ledger_file": "ledger.jsonl",
        "evidence_ladder": [
            {"id": "E0", "rank": 0, "description": "Opinion, desk research, or AI review"},
            {"id": "E1", "rank": 1, "description": "Prototype model or simulation with assumed inputs"},
            {"id": "E2", "rank": 2, "description": "Offline benchmark or replay on real public/historical data"},
            {"id": "E3", "rank": 3, "description": "Controlled pilot or pre-registered experiment on real infrastructure"},
            {"id": "E4", "rank": 4, "description": "Live holdout or production trial with a design partner"},
            {"id": "E5", "rank": 5, "description": "Paying customer, retained or renewed"},
        ],
        "rules": {
            "causal_floor": "E3",
            "strength_floor": "E2",
            "max_source_age_days": 30,
            "due_soon_days": 14,
            "forbidden_causal_verbs": [
                "saves",
                "saved",
                "reduces",
                "reduced",
                "cuts",
                "delivers",
                "improves",
                "accelerates",
                "increases",
            ],
            "allowed_causal_phrases": COMMON_ALLOWED_PHRASES,
        },
        "literature": {
            "default_databases": ["openalex", "crossref", "semanticscholar", "arxiv"],
            "cache_dir": "cache/papers",
            "ezproxy_prefix": "",
        },
        "baselines": [
            {"id": "B0", "label": "Status quo", "description": "What target users run by default today"},
            {"id": "B1", "label": "Strongest free alternative", "description": "Best open-source or built-in setup a competent team can assemble"},
            {"id": "Incumbent", "label": "Leading commercial alternative", "description": "Best funded commercial tool in the segment"},
        ],
        "questions": [
            {"id": "technical_lever", "label": "Technical lever"},
            {"id": "product", "label": "Product"},
            {"id": "services_business", "label": "Services business"},
            {"id": "venture_business", "label": "Venture business"},
            {"id": "career_asset", "label": "Career asset"},
        ],
        "dimensions": [
            {"id": "impact_vs_b0", "label": "Impact vs B0", "kind": "feature_dependent"},
            {"id": "impact_vs_b1", "label": "Impact vs strongest free alternative", "kind": "feature_dependent"},
            {"id": "reliability_safety", "label": "Reliability & safety", "kind": "feature_dependent"},
            {"id": "productability", "label": "Productability", "kind": "feature_dependent"},
            {"id": "market", "label": "Market", "kind": "structural"},
            {"id": "business_model", "label": "Business model", "kind": "structural"},
            {"id": "advantage", "label": "Advantage", "kind": "mixed"},
            {"id": "fundability", "label": "Fundability", "kind": "mixed"},
            {"id": "founder_routes", "label": "Founder routes", "kind": "structural"},
        ],
        "watched_paths": {
            "results": ["docs/notebook", "docs/research"],
            "decisions": ["ROADMAP.md", "docs/adr"],
            "reviews": "reviews/findings.md",
            "consistency_targets": ["ROADMAP.md"],
        },
    },
    "systems": {
        "summary": "Evidence-graded evaluation of a systems/infrastructure lever, product, and business against stock and tuned baselines.",
        "literature": {
            "default_databases": ["sciencedirect", "ieee", "acm", "springer", "arxiv", "openalex"],
            "cache_dir": "cache/papers",
            "ezproxy_prefix": "",
        },
    },
    "ml": {
        "summary": "Evidence-graded evaluation of an ML model/method, product wedge, and defensibility against strong open-weight and API baselines.",
        "evidence_ladder": [
            {"id": "E0", "rank": 0, "description": "Intuition, literature claim, or AI critique"},
            {"id": "E1", "rank": 1, "description": "Toy / synthetic split or single-seed run"},
            {"id": "E2", "rank": 2, "description": "Multi-seed evaluation on held-out public benchmarks vs tuned baseline"},
            {"id": "E3", "rank": 3, "description": "Locked blind holdout or live A/B evaluation on production traffic"},
            {"id": "E4", "rank": 4, "description": "Sustained production deployment at an external partner"},
            {"id": "E5", "rank": 5, "description": "Paying customer retained past renewal"},
        ],
        "literature": {
            "default_databases": ["arxiv", "semanticscholar", "acm", "ieee", "sciencedirect", "openalex"],
            "cache_dir": "cache/papers",
            "ezproxy_prefix": "",
        },
    },
    "saas": {
        "summary": "Evidence-graded evaluation of customer pain, willingness to pay, retention, and moat against existing workflows.",
        "evidence_ladder": [
            {"id": "E0", "rank": 0, "description": "Founder hypothesis, desk research, or AI review"},
            {"id": "E1", "rank": 1, "description": "Customer discovery interviews (past behavior, not hypothetical interest)"},
            {"id": "E2", "rank": 2, "description": "Prototype / concierge workflow run on a prospect's real data"},
            {"id": "E3", "rank": 3, "description": "Active weekly usage or paid pilot with measured outcome"},
            {"id": "E4", "rank": 4, "description": "Repeatable paid conversions across target segment"},
            {"id": "E5", "rank": 5, "description": "Net retention / renewed annual contracts"},
        ],
        "questions": [
            {"id": "problem_severity", "label": "Problem severity"},
            {"id": "product", "label": "Product wedge"},
            {"id": "go_to_market", "label": "Go-to-market"},
            {"id": "venture_business", "label": "Venture scale"},
            {"id": "bootstrapped_business", "label": "Bootstrapped / profitable route"},
        ],
    },
    "research": {
        "summary": "Evidence-graded evaluation of a scientific hypothesis, experimental design, and publication/grant viability.",
        "questions": [
            {"id": "hypothesis", "label": "Core hypothesis"},
            {"id": "method_validity", "label": "Method & identification"},
            {"id": "novelty_vs_prior_art", "label": "Novelty vs prior art"},
            {"id": "publication_venue", "label": "Publication track"},
            {"id": "grant_fit", "label": "Grant / funding fit"},
        ],
        "literature": {
            "default_databases": ["sciencedirect", "springer", "ieee", "acm", "openalex", "crossref", "arxiv"],
            "cache_dir": "cache/papers",
            "ezproxy_prefix": "",
        },
    },
}

FINDINGS_TEMPLATE = """# Review findings

Track internal desk reviews, AI reviews (`E0`, `self-assessed`), and independent human checks (`reviewed`).

## Summary

| ID | Area | Finding | Type | Severity | Reviewer | Status |
|---|---|---|---|---|---|---|
| F1 | Example | Replace with first review finding | question | low | AI desk review | open |
"""


def build_preset_task(preset_name: str = "general", project_name: str | None = None) -> dict[str, Any]:
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_name}'. Choose from: {', '.join(PRESETS)}")
    base = copy.deepcopy(PRESETS["general"])
    if preset_name != "general":
        override = copy.deepcopy(PRESETS[preset_name])
        base.update(override)
    if project_name:
        base["project"] = project_name
    return base

