import argparse
import json
from pathlib import Path

from ontology.benchmark import compare, evaluate
from ontology.data import demo, load_csv, profile
from ontology.proposer import fixture_proposal, propose, synthea_proposal
from ontology.query import live_answer
from ontology.semantic import answer, scoped, visible
from ontology.store import Store


def main():
    parser = argparse.ArgumentParser(description="Review and query a versioned ontology")
    parser.add_argument("--db", default="demo.duckdb")
    parser.add_argument("--store", default="review.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fixture", "profile", "review", "publish", "serve", "demo"):
        sub = commands.add_parser(name)
        if name in {"review", "publish"}:
            sub.add_argument("batch", type=int)
        if name == "serve":
            sub.add_argument("--port", type=int, default=8765)
    commands.add_parser("load").add_argument("csv_dir")
    proposal = commands.add_parser("propose").add_mutually_exclusive_group()
    proposal.add_argument("--offline", action="store_true", help="Use fixture reference proposal")
    proposal.add_argument("--synthea-reference", action="store_true", help="Use Synthea reference proposal")
    decision = commands.add_parser("decide")
    decision.add_argument("change", type=int)
    decision.add_argument("decision", choices=("approve", "reject"))
    query = commands.add_parser("query")
    query.add_argument("request", help="Structured plan JSON or natural language with --live")
    query.add_argument("--role", choices=("admin", "analyst"), default="analyst")
    query.add_argument("--verify", action="store_true")
    query.add_argument("--live", action="store_true")
    query.add_argument("--version", type=int)
    benchmark = commands.add_parser("eval")
    benchmark.add_argument("--live", action="store_true")
    benchmark.add_argument("--synthea", action="store_true")
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    cmd = args.command
    if cmd == "fixture":
        return demo(args.db)
    if cmd == "load":
        return load_csv(args.csv_dir, args.db)
    if cmd == "profile":
        return profile(args.db)
    store = Store(args.store)
    if cmd == "propose":
        adapter = fixture_proposal if args.offline else synthea_proposal if args.synthea_reference else propose
        objects = adapter(args.db)
        return dict(batch=store.stage(args.db, objects))
    if cmd == "review":
        return store.changes(args.batch)
    if cmd == "decide":
        store.decide(args.change, args.decision)
        return dict(decision=args.decision)
    if cmd == "publish":
        return dict(version=store.publish(args.batch))
    if cmd == "query":
        objects = visible(store.snapshot(args.version), args.role)
        if args.live:
            with scoped(args.db, objects) as db:
                return live_answer(db, args.request, objects, args.verify)
        return answer(args.db, objects, json.loads(args.request), args.verify)
    if cmd == "eval":
        results = evaluate(args.db, store.snapshot(), args.live, args.synthea)
        if args.baseline:
            results["regressions"] = compare(results, json.loads(args.baseline.read_text()))
        if args.output:
            args.output.write_text(json.dumps(results, indent=2, default=str) + "\n")
        return {name: {k: v for k, v in report.items() if k != "cases"}
                for name, report in results["results"].items()} | {"regressions": results.get("regressions", {})}
    if cmd == "serve":
        from ontology.server import serve
        return serve(store, args.port)
    if cmd == "demo":
        demo(args.db)
        batch = store.stage(args.db, fixture_proposal(args.db))
        return dict(batch=batch, changes=len(store.changes(batch)),
                    review=f"http://127.0.0.1:8765/?batch={batch}")


if __name__ == "__main__":
    try:
        result = main()
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
    except (KeyError, ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
