import re
from pathlib import Path

import duckdb


def ident(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", name):
        raise ValueError("Unsupported identifier")
    return '"' + name + '"'


def open_db(path, read_only=False):
    return duckdb.connect(str(path), read_only=read_only, config={"enable_external_access": "false", "threads": "2"})


def demo(path):
    db = open_db(path)
    with db:
        db.execute("""
            create or replace table patients as
            select i as id, case when i%2=0 then 'Boston' else 'Salem' end as city,
                   case when i%3=0 then 'F' else 'M' end as gender,
                   'synthetic-private-' || i as private_note
            from range(1,13) t(i);
            create or replace table encounters as
            select i as id, (i-1)%12+1 as patient,
                   case when i%2=0 then 'wellness' else 'ambulatory' end as encounterclass
            from range(1,25) t(i);
            create or replace table claims as
            select i as id, (i-1)%12+1 as patient, i as encounter,
                   50 + i%4*25 as cost, case when i%2=0 then 'paid' else 'pending' end as status
            from range(1,17) t(i);
            create or replace table claim_lines as
            select i+100 as id, (i-1)%16+1 as claim from range(1,34) t(i);
        """)
    return path


def load_csv(folder, path):
    files = sorted(Path(folder).rglob("*.csv"))
    if not files:
        raise ValueError("No CSV files found")
    names = [file.stem.lower() for file in files]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate CSV table names")
    with duckdb.connect(str(path)) as db:
        db.execute("begin")
        for name, file in zip(names, files):
            db.execute(f"create or replace table {ident(name)} as select * from read_csv_auto(?, sample_size=-1)", [str(file)])
        db.execute("commit")


def catalog(db):
    return db.execute("""
        select table_name, column_name, data_type from information_schema.columns
        where table_schema='main' order by table_name, ordinal_position
    """).fetchall()


def sample(db, table, column):
    quoted = ident(column)
    return db.execute(f"select distinct {quoted} from {ident(table)} where {quoted} is not null order by 1 limit 5").fetchall()


def cardinality(db, table, column, target, target_column):
    left, right = ident(column), ident(target_column)
    sql = f"""with counts as (select {right} k, count(*) n from {ident(target)} group by 1)
        select count(*), count(n), coalesce(max(n),0), coalesce(sum(n),0)
        from {ident(table)} l left join counts on l.{left}=counts.k where l.{left} is not null"""
    total, matched, max_matches, joined = db.execute(sql).fetchone()
    return dict(total=total, matched=matched, max_matches=max_matches, joined=joined, sql=sql)


def profile(path):
    with open_db(path, True) as db:
        columns = []
        for table, column, dtype in catalog(db):
            field = ident(column)
            sql = f"select count(*), count({field}), count(distinct {field}) from {ident(table)}"
            rows, populated, distinct = db.execute(sql).fetchone()
            columns.append(dict(table=table, column=column, type=dtype, rows=rows,
                                populated=populated, distinct=distinct, sql=sql))
        joins = []
        for source in columns:
            if source["distinct"] < 2:
                continue
            for target in columns:
                if (source["table"] == target["table"] or source["type"] != target["type"]
                        or not target["rows"] == target["populated"] == target["distinct"]
                        or source["distinct"] > target["distinct"]):
                    continue
                evidence = cardinality(db, source["table"], source["column"], target["table"], target["column"])
                overlap = evidence["matched"] / evidence["total"]
                if overlap >= 0.9:
                    joins.append(dict(source=source["table"], column=source["column"],
                                      target=target["table"], target_column=target["column"],
                                      overlap=overlap, **evidence))
        return dict(columns=columns, joins=joins)
