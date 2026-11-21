from contextlib import contextmanager
from math import isclose

import duckdb

from ontology.data import cardinality, catalog, ident, open_db


def source(metric, where=None, left=False):
    table = ident(metric["table"])
    sql = f"{table} b"
    for index, join in enumerate(metric.get("joins", [])):
        sql += (f" {'left join' if left else 'join'} {ident(join['table'])} j{index}"
                f" on b.{ident(join['left'])}=j{index}.{ident(join['right'])}")
    filters = [clause for clause in (metric.get("where"), where) if clause is not None]
    if filters:
        sql += " where " + " and ".join(f"b.{ident(clause['column'])} = ?" for clause in filters)
    return sql, [clause["value"] for clause in filters]


def rollup(metric, where=None, left=False):
    aggregation = {"sum": "sum", "avg": "avg", "count": "count"}[metric["op"]]
    col = f"b.{ident(metric['column'])}" if metric.get("column") else "*"
    if col == "*" and aggregation != "count":
        raise ValueError("Metric needs a measure column")
    if col != "*":
        col = f"cast({col} as double)" if aggregation in {"sum", "avg"} else col
    relation, params = source(metric, where, left)
    return f"select {aggregation}({col}) from {relation}", params


def validate(path, obj):
    try:
        with open_db(path, True) as db:
            kind, table = obj["kind"], ident(obj["table"])
            if not isinstance(obj["name"], str) or not obj["name"]:
                raise ValueError("A name is required")
            if not isinstance(obj["roles"], list) or not set(obj["roles"]) <= {"analyst", "admin"}:
                raise ValueError("Unknown roles")
            if not 0 <= obj["confidence"] <= 1:
                raise ValueError("Confidence out of bounds")
            if kind == "property":
                db.execute(f"select {ident(obj['column'])} from {table} limit 0")
                return {"ok": True}
            if kind == "join":
                result = cardinality(db, obj["table"], obj["left"], obj["target"], obj["right"])
                return dict(result, ok=result["total"] > 0 and result["matched"] == result["total"]
                            and result["max_matches"] == 1)
            if kind not in {"entity", "metric"}:
                raise ValueError("Unknown change kind")
            key = ident(obj["key"])
            total, distinct = db.execute(f"select count(*), count(distinct {key}) from {table}").fetchone()
            if total == 0 or distinct != total:
                return dict(ok=False, reason="Base key is empty, nullable, or nonunique")
            if kind == "entity":
                return dict(ok=True, rows=total)
            base = dict(obj, joins=[])
            base_source, base_params = source(base)
            expected = db.execute(f"select count(*) from {base_source}", base_params).fetchone()[0]
            joined, params = source(obj)
            measured = f"count(b.{ident(obj['column'])})" if obj.get("column") else "count(*)"
            grain_sql = f"select count(*), count(distinct b.{key}), {measured} from {joined}"
            rows, distinct_keys, nonnull = db.execute(grain_sql, params).fetchone()
            sql, params = rollup(obj)
            value = db.execute(sql, params).fetchone()[0]
            return dict(ok=rows == distinct_keys == expected == nonnull and value is not None,
                        rows=rows, distinct_keys=distinct_keys, expected_rows=expected,
                        null_rate=(rows-nonnull)/rows if rows else None,
                        value=value, sql=sql, params=params, grain_sql=grain_sql)
    except (KeyError, ValueError, TypeError, AttributeError, duckdb.Error) as exc:
        return dict(ok=False, reason=str(exc))


def dependencies(obj):
    table = obj["table"]
    fields = {(table, obj[k]) for k in ("key", "column", "left") if obj.get(k) }
    if obj.get("where"):
        fields.add((table, obj["where"]["column"]))
    if obj["kind"] == "join":
        fields.add((obj["target"], obj["right"]))
    for join in obj.get("joins", []):
        fields.update({(table, join["left"]), (join["table"], join["right"])})
    return fields


def visible(objects, role):
    allowed = [obj for obj in objects if role in obj.get("roles", [])]
    properties = {(obj["table"], obj["column"]) for obj in allowed if obj["kind"] == "property"}
    return [{k: v for k, v in obj.items() if k != "evidence"} for obj in allowed
            if dependencies(obj) <= properties]


@contextmanager
def scoped(path, objects):
    columns = {}
    for obj in objects:
        if obj["kind"] == "property":
            columns.setdefault(obj["table"], set()).add(obj["column"])
    with open_db(path, True) as source_db, open_db(":memory:") as target:
        source_types = {(t, c): dtype for t, c, dtype in catalog(source_db)}
        for table, names in sorted(columns.items()):
            names = sorted(names)
            fields = ", ".join(ident(name) for name in names)
            declarations = ", ".join(f"{ident(name)} {source_types[table, name]}" for name in names)
            target.execute(f"create table {ident(table)} ({declarations})")
            rows = source_db.execute(f"select {fields} from {ident(table)}")
            vectors = ", ".join(f"unnest(?::{source_types[table, name]}[])" for name in names)
            while batch := rows.fetchmany(4096):
                target.execute(f"insert into {ident(table)} select {vectors}",
                               [list(column) for column in zip(*batch)])
        yield target


def equivalent(a, b):
    if a is None or b is None:
        return a is b
    return isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def independent(db, metric, where):
    base, params = source(dict(metric, joins=[]), where)
    if metric.get("column"):
        projection = f"b.{ident(metric['column'])}"
    else:
        projection = "1"
    rows = db.execute(f"select {projection} from {base}", params).fetchall()
    nonnull = [float(value) for (value,) in rows if value is not None] if metric["op"] != "count" else []
    if metric["op"] == "count":
        return sum(value is not None for (value,) in rows)
    if not nonnull:
        return None
    total = sum(nonnull)
    return total if metric["op"] == "sum" else total / len(nonnull)


def execute_plan(db, objects, plan, verify=False):
    metrics = {obj["name"]: obj for obj in objects if obj["kind"] == "metric"}
    if plan.get("metric") not in metrics:
        raise ValueError("Metric unavailable for this role")
    metric = metrics[plan["metric"]]
    where = plan.get("where")
    if where is not None:
        if not isinstance(where, dict) or set(where) != {"column", "value"}:
            raise ValueError("Unsupported filter")
        allowed = {(o["table"], o["column"]) for o in objects if o["kind"] == "property"}
        if (metric["table"], where["column"]) not in allowed:
            raise ValueError("Filter unavailable for this role")
    sql, params = rollup(metric, where)
    value = db.execute(sql, params).fetchone()[0]
    if verify and not equivalent(value, independent(db, metric, where)):
        return dict(status="disagreement", sql=sql)
    return dict(status="ok", value=value, sql=sql, params=params)


def answer(path, objects, plan, verify=False):
    with scoped(path, objects) as db:
        return execute_plan(db, objects, plan, verify)
