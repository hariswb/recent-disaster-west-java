import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="recentdisaster", description="West Java recent-incident monitor")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, classify, extract and write docs/kebencanaan-jabar/data/incidents.json")
    r.add_argument("--no-llm", action="store_true", help="rules only")
    r.add_argument("--dry-run", action="store_true", help="do not write output or cache")
    r.add_argument("--sources", help="comma-separated source ids (also runs disabled ones)")

    sub.add_parser("llm-check", help="test each configured LLM provider")
    sub.add_parser("sources", help="fetch each source and report item counts")

    args = ap.parse_args(argv)
    from recentdisaster.config import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)

    if args.cmd == "run":
        from recentdisaster.pipeline import run

        out = run(
            use_llm=not args.no_llm,
            dry_run=args.dry_run,
            only_sources=args.sources.split(",") if args.sources else None,
        )
        for s in out["source_status"]:
            mark = "ok " if s["ok"] else "ERR"
            print(f"  [{mark}] {s['id']:<28} {s['items']:>4} items  {s['seconds']:>5}s  {s['error'] or ''}")
        print("stats:", json.dumps(out["stats"]))
        for p in out["llm_providers"]:
            print(f"  llm {p['name']}: calls={p['calls']} exhausted={p['exhausted']}")
        if args.dry_run:
            print("\ncandidates:")
            for title, cat, kab in out["_candidates"]:
                print(f"  - [{cat}] {kab or '?'} :: {title}")
        print("\nincidents:")
        for i in out["incidents"]:
            v = ", ".join(f"{k}={n}" for k, n in i["victims"].items())
            print(f"  - [{i['category']}] {i['kab_kota'] or '?'} / {i['kecamatan'] or '-'} :: {i['title']} "
                  f"({i['report_count']} src, {i['extraction']}) {v}")

    elif args.cmd == "llm-check":
        from recentdisaster.llm import check

        ok = False
        for row in check():
            print(f"{row['name']:<14} {row['status']}")
            for e in row.get("errors", []):
                print(f"    error: {e}")
            if "available_models" in row:
                print(f"    available models: {row['available_models']}")
            ok |= row["status"].startswith("OK")
        sys.exit(0 if ok else 1)

    elif args.cmd == "sources":
        from recentdisaster.sources import fetch_all, load_sources

        _, statuses = fetch_all(load_sources(include_disabled=True), known=set())
        for s in statuses:
            print(f"  [{'ok ' if s['ok'] else 'ERR'}] {s['id']:<28} {s['items']:>4} items {s['error'] or ''}")
