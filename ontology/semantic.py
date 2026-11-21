import json
import os
import stat
from contextlib import contextmanager
from hashlib import sha256
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from ontology.data import cardinality, ident, open_db


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
            base_sql, base_params = rollup(base)
            expected, base_value = db.execute(
                f"select count(*), {base_sql.removeprefix('select ')}", base_params).fetchone()
            joined, params = source(obj)
            measured = f"count(b.{ident(obj['column'])})" if obj.get("column") else "count(*)"
            grain_sql = f"select count(*), count(distinct b.{key}), {measured} from {joined}"
            rows, distinct_keys, nonnull = db.execute(grain_sql, params).fetchone()
            sql, params = rollup(obj)
            value = db.execute(sql, params).fetchone()[0]
            return dict(ok=rows == distinct_keys == expected == nonnull and value is not None,
                        rows=rows, distinct_keys=distinct_keys, expected_rows=expected,
                        null_rate=(rows-nonnull)/rows if rows else None,
                        value=value, base_value=base_value, sql=sql, params=params, grain_sql=grain_sql)
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


def source_revision(path):
    info = path.stat()
    if path.with_name(path.name + ".wal").exists():
        return None
    return (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def build_projection(path, projection, columns):
    source_path = str(path).replace("'", "''")
    with duckdb.connect(str(projection), config={"threads": "2"}) as builder:
        builder.execute(f"attach '{source_path}' as source_data (read_only)")
        builder.execute("begin")
        for table, names in sorted(columns.items()):
            fields = ", ".join(ident(name) for name in sorted(names))
            builder.execute(f"create table {ident(table)} as "
                            f"select {fields} from source_data.main.{ident(table)}")
        builder.execute("commit")


@contextmanager
def scoped(path, objects, *, cache_dir=None, version=None, role=None):
    path = Path(path).resolve()
    columns = {}
    for obj in objects:
        if obj["kind"] == "property":
            columns.setdefault(obj["table"], set()).add(obj["column"])
    revision = source_revision(path) if cache_dir is not None else None
    destination = None
    if revision is not None and version is not None and role is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = cache_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Projection cache directory must be private and owned by the current user")
        identity = json.dumps([1, revision, version, role, objects], sort_keys=True, default=str)
        destination = cache_dir / (sha256(identity.encode()).hexdigest() + ".duckdb")
        if destination.is_file() and source_revision(path) == revision:
            with open_db(destination, True) as session:
                yield session
            return
    with TemporaryDirectory(prefix="ontology-role-", dir=cache_dir if destination else None) as folder:
        projection = Path(folder) / "projection.duckdb"
        build_projection(path, projection, columns)
        if destination is not None and source_revision(path) == revision:
            projection.chmod(0o600)
            os.replace(projection, destination)
            projection = destination
        with open_db(projection, True) as session:
            yield session


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


def execute_plan(db, objects, plan, verify=False, *, paired=False):
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
    primary = dict(status="ok", value=value, sql=sql, params=params)
    checked = primary
    if (verify or paired) and not equivalent(value, independent(db, metric, where)):
        checked = dict(status="disagreement", sql=sql, verifier_reason="different_value")
    return (primary, checked) if paired else checked


def answer(path, objects, plan, verify=False):
    with scoped(path, objects) as db:
        return execute_plan(db, objects, plan, verify)
