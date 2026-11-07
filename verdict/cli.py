"""Command-line interface for the standalone `verdict` system."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import __version__
from .checker import check_entry, check_ledger
from .differ import diff_entries, format_diff_text, scan_workspace_since
from .html_report import build_html_report
from .literature import (
    SUPPORTED_DBS,
    fetch_work,
    load_lit_config,
    parse_db_list,
    run_netcheck,
    search_literature,
)
from .markdown import render_verdict_markdown
from .presets import FINDINGS_TEMPLATE, PRESETS, build_preset_task
from .schema import TaskConfig, load_ledger, load_task, parse_date, save_ledger_entry


def _parse_as_of(val: str | None) -> dt.date | None:
    if not val:
        return None
    d = parse_date(val)
    if not d:
        raise SystemExit(f"Invalid --as-of date '{val}'; expected YYYY-MM-DD.")
    return d


def _try_load_task(task_arg: str | None) -> TaskConfig | None:
    try:
        return load_task(task_arg)
    except FileNotFoundError:
        return None


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    task_file = target / "task.json"
    if task_file.exists() and not args.force:
        print(f"Refusing to overwrite existing {task_file} (pass --force to overwrite).", file=sys.stderr)
        return 1

    data = build_preset_task(preset_name=args.preset, project_name=args.name or target.name)
    task_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ledger_file = target / data.get("ledger_file", "ledger.jsonl")
    if not ledger_file.exists():
        ledger_file.write_text("", encoding="utf-8")

    if args.with_findings:
        findings_path = (target / data["workspace_root"] / data["watched_paths"]["reviews"]).resolve()
        if not findings_path.exists():
            findings_path.parent.mkdir(parents=True, exist_ok=True)
            findings_path.write_text(FINDINGS_TEMPLATE, encoding="utf-8")
            print(f"Created review log: {findings_path}")

    print(f"Initialized verdict task ('{args.preset}' preset) for '{data['project']}' at {task_file}")
    print(f"Ledger: {ledger_file}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    entries = load_ledger(task.ledger_path)
    as_of = _parse_as_of(args.as_of)
    issues = check_ledger(entries, task, entry_filter=args.entry, as_of=as_of, strict_dates=args.strict_dates)
    errors = [i for i in issues if i.severity == "ERROR"]
    warns = [i for i in issues if i.severity == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "project": task.project,
                    "entries_checked": len(entries) if not args.entry else 1,
                    "errors": len(errors),
                    "warnings": len(warns),
                    "issues": [i.to_dict() for i in issues],
                },
                indent=2,
            )
        )
    else:
        print(f"verdict check [{task.project}]: {len(entries)} ledger run(s) · {len(errors)} error(s), {len(warns)} warning(s)")
        for iss in issues:
            print(f"  [{iss.severity}] {iss.entry_id} :: {iss.location} ({iss.code}) — {iss.message}")
    return 1 if errors else 0


def cmd_diff(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    entries = load_ledger(task.ledger_path)
    as_of = _parse_as_of(args.as_of)

    entry_diff = None
    if len(entries) >= 2 or (args.from_entry and args.to_entry):
        if args.from_entry and args.to_entry:
            prev = next((e for e in entries if e["id"] == args.from_entry or e.get("date") == args.from_entry), None)
            curr = next((e for e in entries if e["id"] == args.to_entry or e.get("date") == args.to_entry), None)
            if not prev or not curr:
                print("Could not find --from / --to entries in ledger.", file=sys.stderr)
                return 1
        else:
            prev, curr = entries[-2], entries[-1]
        entry_diff = diff_entries(prev, curr, task)

    latest = entries[-1] if entries else None
    ws_scan = scan_workspace_since(latest, task, as_of=as_of)
    if args.json:
        print(json.dumps({"entry_diff": entry_diff, "workspace": ws_scan}, indent=2))
    else:
        print(format_diff_text(task, entry_diff, ws_scan), end="")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    candidate_path = Path(args.entry_json).expanduser().resolve()
    entry = json.loads(candidate_path.read_text(encoding="utf-8"))
    as_of = _parse_as_of(args.as_of) or parse_date(entry.get("date"))

    issues = check_entry(entry, task, as_of=as_of, strict_dates=False, check_horizon=True)
    errors = [i for i in issues if i.severity == "ERROR"]
    if errors and not args.force:
        print(f"Refusing to record '{entry.get('id')}': {len(errors)} epistemic/schema error(s):", file=sys.stderr)
        for iss in errors:
            print(f"  [ERROR] {iss.location} ({iss.code}) — {iss.message}", file=sys.stderr)
        return 1

    action = save_ledger_entry(task.ledger_path, entry)
    print(f"Ledger {action}: {entry['id']} -> {task.ledger_path}")
    for iss in issues:
        if iss.severity == "WARN":
            print(f"  [WARN] {iss.location} ({iss.code}) — {iss.message}")

    if args.write_md:
        md_path = task.verdicts_dir / f"{entry['id']}.md"
        if md_path.exists() and not args.overwrite_md:
            print(f"Skipped Markdown write because {md_path} already exists (pass --overwrite-md to replace).", file=sys.stderr)
        else:
            md_path.write_text(render_verdict_markdown(entry, task), encoding="utf-8")
            print(f"Wrote Markdown verdict -> {md_path}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    entries = load_ledger(task.ledger_path)
    if not entries:
        print("Ledger is empty.", file=sys.stderr)
        return 1
    target = (
        entries[-1]
        if (not args.entry or args.entry == "latest")
        else next((e for e in entries if e["id"] == args.entry or e.get("date") == args.entry), None)
    )
    if not target:
        print(f"Entry '{args.entry}' not found.", file=sys.stderr)
        return 1
    md = render_verdict_markdown(target, task)
    if args.out:
        out_p = Path(args.out).expanduser().resolve()
        out_p.write_text(md, encoding="utf-8")
        print(f"Wrote {out_p}")
    else:
        print(md, end="")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    entries = load_ledger(task.ledger_path)
    as_of = _parse_as_of(args.as_of)
    out_path = Path(args.out).expanduser().resolve() if args.out else (task.verdicts_dir / "report.html")
    html_doc = build_html_report(task, entries, as_of=as_of)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Built HTML verdict report ({len(entries)} run(s)) -> {out_path}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    task = _try_load_task(args.task)
    cfg = load_lit_config(task.literature if task else None, base_dir=task.verdicts_dir if task else Path.cwd())
    default_dbs = task.literature.get("default_databases") if task else None
    try:
        dbs = parse_db_list(args.db, default_dbs=default_dbs)
        works, notes = search_literature(
            args.query,
            cfg,
            dbs=dbs,
            since_year=args.since_year,
            limit=args.limit,
            oa_only=args.oa_only,
            allow_raw_query=args.allow_raw_query,
        )
    except ValueError as exc:
        print(f"verdict search error: {exc}", file=sys.stderr)
        return 1

    today = str(dt.date.today())
    if args.json:
        print(
            json.dumps(
                {
                    "query": args.query,
                    "databases": dbs,
                    "checked_date": today,
                    "notes": notes,
                    "count": len(works),
                    "results": [w.to_dict(today) for w in works],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"verdict search ({', '.join(dbs)}): {len(works)} result(s) for {args.query!r} [checked {today}]")
    for n in notes:
        print(f"  note: {n}")
    for idx, w in enumerate(works, start=1):
        auth = (w.authors[0] + (" et al." if len(w.authors) > 1 else "")) if w.authors else "Unknown"
        oa_tag = " [OA]" if w.is_open_access else ""
        cites = f" · {w.citations} cites" if w.citations is not None else ""
        print(f"\n{idx}. {w.title} ({w.year or 'n.d.'}){oa_tag}{cites}")
        print(f"   {auth} — {w.venue or w.publisher or w.source_db} [{w.source_db}]")
        if w.doi:
            print(f"   DOI: {w.doi}  (fetch: verdict fetch {w.doi})")
        elif w.url:
            print(f"   URL: {w.url}")
        if w.pdf_url:
            print(f"   PDF: {w.pdf_url}")
        if w.ezproxy_url:
            print(f"   Campus EZproxy: {w.ezproxy_url}")
        if w.abstract:
            snippet = w.abstract[:280] + ("…" if len(w.abstract) > 280 else "")
            print(f"   Abstract: {snippet}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    task = _try_load_task(args.task)
    cfg = load_lit_config(task.literature if task else None, base_dir=task.verdicts_dir if task else Path.cwd())
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    result = fetch_work(args.identifier, cfg, out_dir=out_dir, extract_text=not args.no_extract_text)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"verdict fetch [{result['status']}]: {result['message']}")
        if result.get("saved_text"):
            print(f"  Text: {result['saved_text']}")
        if result.get("saved_pdf"):
            print(f"  PDF:  {result['saved_pdf']}")
        if result.get("ezproxy_url"):
            print(f"  EZproxy URL: {result['ezproxy_url']}")
        for att in result.get("attempts", []):
            print(f"  attempt: {att}")
    return 0 if result["status"] == "full_text" else 1


def cmd_netcheck(args: argparse.Namespace) -> int:
    task = _try_load_task(args.task)
    cfg = load_lit_config(task.literature if task else None, base_dir=task.verdicts_dir if task else Path.cwd())
    report = run_netcheck(cfg, sample_sd_doi=args.sample_doi)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("== verdict netcheck (university network & scholarly API status) ==")
    for p in report["probes"]:
        mark = "OK" if p["reachable"] else "FAIL"
        detail = f"HTTP {p.get('http_status')}" if "http_status" in p else p.get("error", "")
        print(f"  [{mark}] {p['service']}: {detail}")
    sd = report["sciencedirect_campus_status"]
    print(f"  ScienceDirect: {sd['note']}")
    creds = [k for k, v in report["credentials_configured"].items() if v]
    print(f"  Configured keys/vars: {', '.join(creds) if creds else 'none (using keyless open/DOI indexes)'}")
    prox = report["proxies_detected"]
    print(f"  Proxy: HTTPS_PROXY={'yes' if prox['https_proxy'] else 'no'}, EZproxy={prox['ezproxy_prefix'] or 'none'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="verdict",
        description="Evidence-graded verdict ledger, epistemic checker, campus academic search, and HTML trajectory report.",
    )
    ap.add_argument("-V", "--version", action="version", version=f"verdict {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize a new project task.json and empty ledger.jsonl")
    p_init.add_argument("directory", help="Directory to initialize")
    p_init.add_argument("--name", help="Project name")
    p_init.add_argument("--preset", choices=sorted(PRESETS.keys()), default="general", help="Domain preset")
    p_init.add_argument("--with-findings", action="store_true", help="Also scaffold a starter reviews/findings.md")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing task.json")
    p_init.set_defaults(func=cmd_init)

    p_chk = sub.add_parser("check", help="Validate ledger entries against epistemic, provenance, and horizon rules")
    p_chk.add_argument("--task", help="Path to task.json (auto-detected if omitted)")
    p_chk.add_argument("--entry", help="Specific entry id/date or 'latest' (default: all)")
    p_chk.add_argument("--as-of", help="Reference date YYYY-MM-DD for horizon/freshness checks")
    p_chk.add_argument("--strict-dates", action="store_true", help="Promote overdue kill dates & stale sources to ERROR")
    p_chk.add_argument("--json", action="store_true", help="Emit JSON output")
    p_chk.set_defaults(func=cmd_check)

    p_diff = sub.add_parser("diff", help="Show longitudinal verdict shifts and workspace changes since last verdict")
    p_diff.add_argument("--task", help="Path to task.json")
    p_diff.add_argument("--from", dest="from_entry", help="Earlier entry id/date")
    p_diff.add_argument("--to", dest="to_entry", help="Later entry id/date")
    p_diff.add_argument("--as-of", help="Reference date YYYY-MM-DD")
    p_diff.add_argument("--json", action="store_true", help="Emit JSON output")
    p_diff.set_defaults(func=cmd_diff)

    p_rec = sub.add_parser("record", help="Validate and upsert a structured verdict JSON entry into ledger.jsonl")
    p_rec.add_argument("entry_json", help="Path to candidate verdict JSON file")
    p_rec.add_argument("--task", help="Path to task.json")
    p_rec.add_argument("--as-of", help="Reference date YYYY-MM-DD")
    p_rec.add_argument("--write-md", action="store_true", help="Also write <verdicts_dir>/<id>.md")
    p_rec.add_argument("--overwrite-md", action="store_true", help="Allow overwriting an existing <id>.md")
    p_rec.add_argument("--force", action="store_true", help="Record even if epistemic checker reports errors")
    p_rec.set_defaults(func=cmd_record)

    p_ren = sub.add_parser("render", help="Render a ledger entry as canonical Markdown")
    p_ren.add_argument("--task", help="Path to task.json")
    p_ren.add_argument("--entry", default="latest", help="Entry id/date or 'latest'")
    p_ren.add_argument("--out", help="Write Markdown to file instead of stdout")
    p_ren.set_defaults(func=cmd_render)

    p_rep = sub.add_parser("report", help="Compile ledger history into a standalone interactive report.html")
    p_rep.add_argument("--task", help="Path to task.json")
    p_rep.add_argument("--out", help="Output HTML path (default: <verdicts_dir>/report.html)")
    p_rep.add_argument("--as-of", help="Reference date YYYY-MM-DD")
    p_rep.set_defaults(func=cmd_report)

    p_srch = sub.add_parser(
        "search",
        help=f"Search academic databases ({', '.join(SUPPORTED_DBS)}) directly from your machine/campus IP",
    )
    p_srch.add_argument("query", help="Search query (must not contain private paths or secrets)")
    p_srch.add_argument("--db", default="all", help="Comma-separated DBs: all, sciencedirect, ieee, acm, springer, arxiv, semanticscholar, openalex, crossref")
    p_srch.add_argument("--since-year", type=int, help="Minimum publication year (e.g. 2024)")
    p_srch.add_argument("--limit", type=int, default=10, help="Max deduplicated results (default: 10)")
    p_srch.add_argument("--oa-only", action="store_true", help="Only return open-access or PDF-linked papers")
    p_srch.add_argument("--allow-raw-query", action="store_true", help="Bypass query privacy check")
    p_srch.add_argument("--task", help="Optional path to task.json for literature settings")
    p_srch.add_argument("--json", action="store_true", help="Emit structured JSON with ready-to-cite source snippets")
    p_srch.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser(
        "fetch",
        help="Fetch full text or PDF for a DOI, arXiv ID, or URL over university network / OA repositories",
    )
    p_fetch.add_argument("identifier", help="DOI (e.g. 10.1016/...), arXiv:ID, or PDF URL")
    p_fetch.add_argument("--out", "--out-dir", dest="out_dir", help="Directory to save downloaded PDF / extracted text")
    p_fetch.add_argument("--no-extract-text", action="store_true", help="Skip pdftotext extraction")
    p_fetch.add_argument("--task", help="Optional path to task.json")
    p_fetch.add_argument("--json", action="store_true", help="Emit JSON output")
    p_fetch.set_defaults(func=cmd_fetch)

    p_net = sub.add_parser("netcheck", help="Check academic DB reachability, API keys, and university IP entitlement")
    p_net.add_argument("--sample-doi", default="10.1016/j.future.2023.01.001", help="Closed ScienceDirect DOI to probe campus entitlement")
    p_net.add_argument("--task", help="Optional path to task.json")
    p_net.add_argument("--json", action="store_true", help="Emit JSON output")
    p_net.set_defaults(func=cmd_netcheck)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

