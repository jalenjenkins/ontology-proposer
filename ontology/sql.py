from sqlglot import exp, parse


def read_sql(db, sql):
    statements = parse(sql, read="duckdb")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("Expected one SELECT")
    query = statements[0]
    if any(not isinstance(table.this, exp.Identifier) for table in query.find_all(exp.Table)):
        raise ValueError("Table functions are unavailable")
    if query.find(exp.Into) or query.find(exp.Command) or query.find(exp.Anonymous):
        raise ValueError("Unsupported SQL expression")
    return db.execute(f"select * from ({query.sql(dialect='duckdb')}) q limit 100").fetchall()
