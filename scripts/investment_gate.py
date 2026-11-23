"""Run the synthetic investment release walkthrough with the existing gate."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ontology.data import load_csv
from ontology.gate import compare_replays, load_suite
from ontology.semantic import answer, visible
from ontology.store import Store

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "investment"


def run():
    suite = EXAMPLE / "gate-cases.json"
    cases = load_suite(suite)
    common = dict(table="positions", roles=["analyst", "admin"], confidence=1)
    properties = [
        dict(common, kind="property", name=f"positions.{column}", column=column)
        for column in ("position_id", "snapshot_id", "market_value_usd", "purchase_cost_usd")
    ]
    metric = dict(common, kind="metric", name="fund_market_value",
                  key="position_id", op="sum", column="market_value_usd")
    count = dict(common, kind="metric", name="position_count", key="position_id", op="count")
    duplicated = dict(metric, name="duplicated_market_value", joins=[
        dict(table="security_tags", left="security_id", right="security_id")
    ])
    replacement = dict(metric, column="purchase_cost_usd")

    with TemporaryDirectory(prefix="ontology-investment-") as folder:
        source = Path(folder) / "investment.duckdb"
        load_csv(EXAMPLE / "csv", source)
        store = Store(Path(folder) / "review.sqlite")
        bootstrap = store.stage(source, [*properties, metric, count, duplicated])
        for change in store.changes(bootstrap):
            if change["after"]["name"] == duplicated["name"]:
                evidence = change["validation"]
                if evidence["ok"]:
                    raise RuntimeError("Expected the validator to detect duplicate positions")
                print(f"Validator rejected tag join: {evidence['rows']} rows for "
                      f"{evidence['distinct_keys']} positions")
                store.decide(change["id"], "reject")
            else:
                store.decide(change["id"], "approve")
        store.publish(bootstrap)

        update = store.stage(source, [replacement])
        change, = store.changes(update)
        store.decide(change["id"], "approve")
        print("Purchase-cost proposal passed structural validation.")
        report = store.check_gate(update, source, suite)
        for finding in report["findings"]:
            print(f"  {finding['id']} ({finding['role']}): "
                  f"${finding['before']:,.0f} -> ${finding['after']:,.0f}")

        try:
            store.publish(update, source, suite)
        except ValueError as error:
            if str(error) != "Acknowledge the answer changes before publishing":
                raise
            print(f"Publication held: {error}")
        else:
            raise RuntimeError("Published an answer change without acknowledgement")

        version, baseline = store.versioned_snapshot()
        alpha = cases[0]
        live = answer(source, visible(baseline, alpha["role"]), alpha["plan"], verify=True)
        print(f"Published v{version} still returns ${live['value']:,.0f} for alpha-close.")
        candidate = [replacement if obj["name"] == metric["name"] else obj for obj in baseline]
        comparison = compare_replays(source, baseline, candidate, cases)
        for mode in ("targeted", "full"):
            result = comparison[mode]
            print(f"{mode.capitalize()} replay: {result['checked']}/{result['cases']} checks, "
                  f"{len(result['findings'])} findings")
        print(f"Findings missed by targeted replay: {len(comparison['missed'])}")


if __name__ == "__main__":
    run()
