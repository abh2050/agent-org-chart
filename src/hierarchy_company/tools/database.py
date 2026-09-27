"""Database team tools: SQL analysis (sqlglot), EXPLAIN parsing, and read-only Postgres integration."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import sqlglot
from langchain_core.tools import tool
from sqlglot import exp

from hierarchy_company.tools._common import needs_input, report, unavailable

DB_ENV = "HC_DATABASE_URL"
_IDENT = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")


def _parse(query: str) -> exp.Expression:
    try:
        parsed = sqlglot.parse_one(query, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc
    if parsed is None:
        raise ValueError("no SQL statement found")
    return cast(exp.Expression, parsed)


# ── SQL Agent ────────────────────────────────────────────────────────
@tool
def analyze_sql_query(query: str) -> str:
    """Statically analyze a SQL query (PostgreSQL dialect) for performance and safety anti-patterns:
    SELECT *, functions wrapped around filtered columns, leading-wildcard LIKE, OFFSET pagination,
    NOT IN subqueries, OR chains and UPDATE/DELETE without WHERE."""
    try:
        tree = _parse(query)
    except ValueError as exc:
        return needs_input("analyze_sql_query", str(exc))
    findings: list[str] = []
    if isinstance(tree, exp.Update | exp.Delete) and not tree.args.get("where"):
        findings.append(f"{tree.key.upper()} without WHERE affects every row")
    for select in tree.find_all(exp.Select):
        if any(isinstance(e, exp.Star) for e in select.expressions):
            findings.append("SELECT * reads every column (prevents index-only scans, breaks on schema change)")
        if select.args.get("offset"):
            findings.append(f"OFFSET {select.args['offset'].expression.sql()} pagination scans and discards rows; "
                            "use keyset pagination (WHERE sort_key < :last ORDER BY sort_key LIMIT n)")
    where_nodes = list(tree.find_all(exp.Where))
    for where in where_nodes:
        for func in where.find_all(exp.Func):
            if isinstance(func, exp.Binary | exp.Predicate | exp.Connector):
                continue
            col = func.find(exp.Column)
            if col is not None:
                findings.append(f"{func.sql()} in WHERE wraps column '{col.sql()}' so a plain index on it cannot "
                                "be used (use a range predicate or an expression index)")
        ors = len(list(where.find_all(exp.Or)))
        if ors >= 3:
            findings.append(f"{ors} OR conditions in WHERE; consider IN (...) or UNION ALL")
    for like in tree.find_all(exp.Like, exp.ILike):
        pattern = like.expression
        if isinstance(pattern, exp.Literal) and pattern.this.startswith("%"):
            findings.append(f"{like.sql()} has a leading wildcard; B-tree indexes cannot help (use pg_trgm)")
    for node in tree.find_all(exp.Not):
        if isinstance(node.this, exp.In) and node.this.args.get("query"):
            findings.append("NOT IN (subquery) returns no rows if the subquery yields NULL; use NOT EXISTS")
    tables = sorted({t.name for t in tree.find_all(exp.Table)})
    return report("SQL analysis", findings, f"statement={tree.key.upper()}, tables={tables}.")


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    return {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}


def _table_of(col: exp.Column, aliases: dict[str, str]) -> str | None:
    if col.table:
        return aliases.get(col.table, col.table)
    return next(iter(aliases.values())) if len(set(aliases.values())) == 1 else None


# ── Schema Agent ─────────────────────────────────────────────────────
@tool
def suggest_indexes(query: str) -> str:
    """Derive B-tree index recommendations from a SQL query: equality columns first, then one range column,
    then ORDER BY columns, per table; plus indexes for join keys. Returns CREATE INDEX CONCURRENTLY statements."""
    try:
        tree = _parse(query)
    except ValueError as exc:
        return needs_input("suggest_indexes", str(exc))
    aliases = _alias_map(tree)
    eq: dict[str, list[str]] = {}
    rng: dict[str, list[str]] = {}
    order: dict[str, list[str]] = {}

    def add(bucket: dict[str, list[str]], col: exp.Column) -> None:
        table = _table_of(col, aliases)
        if table and col.name not in bucket.setdefault(table, []):
            bucket[table].append(col.name)

    for where in tree.find_all(exp.Where):
        for cmp in where.find_all(exp.EQ, exp.In):
            col = cmp.this if isinstance(cmp.this, exp.Column) else None
            other = cmp.expression if isinstance(cmp, exp.EQ) else None
            if col is not None and not isinstance(other, exp.Column):
                add(eq, col)
        for rcmp in where.find_all(exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between):
            if isinstance(rcmp.this, exp.Column):
                add(rng, rcmp.this)
    for ordered in tree.find_all(exp.Ordered):
        if isinstance(ordered.this, exp.Column):
            add(order, ordered.this)

    statements: list[str] = []
    for table in sorted(set(eq) | set(rng) | set(order)):
        cols = list(eq.get(table, []))
        cols += [c for c in rng.get(table, [])[:1] if c not in cols]
        if not rng.get(table):
            cols += [c for c in order.get(table, []) if c not in cols]
        if cols:
            statements.append(f"CREATE INDEX CONCURRENTLY idx_{table}_{'_'.join(cols)} ON {table} ({', '.join(cols)});")
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        for jcmp in on.find_all(exp.EQ) if on else []:
            if isinstance(jcmp.this, exp.Column) and isinstance(jcmp.expression, exp.Column):
                for col in (jcmp.this, jcmp.expression):
                    jtable = _table_of(col, aliases)
                    stmt = f"CREATE INDEX CONCURRENTLY idx_{jtable}_{col.name} ON {jtable} ({col.name});"
                    covered = any(s.startswith(f"CREATE INDEX CONCURRENTLY idx_{jtable}_") and f"({col.name}" in s
                                  for s in statements)
                    if jtable and col.name != "id" and not covered:
                        statements.append(stmt)
    if not statements:
        return "Index suggestions: no filter, join or sort columns found that an index would serve."
    return ("Index suggestions (equality → range → sort; verify with EXPLAIN):\n" + "\n".join(statements))


# ── Query Optimization Agent ─────────────────────────────────────────
@tool
def analyze_explain_plan(plan: str) -> str:
    """Parse PostgreSQL EXPLAIN / EXPLAIN ANALYZE text output and flag sequential scans on large tables,
    filters that discard most rows, sorts spilling to disk, row-estimate misses and nested loops with many
    iterations. Pass the plan text exactly as psql printed it."""
    if not re.search(r"(Scan|Join|Sort|Aggregate|Loop|Result)", plan):
        return needs_input("analyze_explain_plan", "PostgreSQL EXPLAIN output text")
    findings: list[str] = []
    for m in re.finditer(r"Seq Scan on (\w+).*?rows=(\d+)(?:.*?actual time=[\d.]+\.\.([\d.]+) rows=(\d+))?", plan):
        table, est, actual_ms, actual_rows = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        rows = int(actual_rows) if actual_rows else est
        if rows >= 10_000:
            findings.append(f"Seq Scan on {table} over ~{rows:,} rows" + (f" ({actual_ms} ms)" if actual_ms else ""))
        if actual_rows and est and max(est, int(actual_rows)) / max(1, min(est, int(actual_rows))) >= 10:
            findings.append(f"row estimate miss on {table}: planned {est:,}, actual {int(actual_rows):,} (run ANALYZE)")
    for m in re.finditer(r"Rows Removed by Filter: (\d+)", plan):
        if int(m.group(1)) >= 10_000:
            findings.append(f"filter discarded {int(m.group(1)):,} rows; an index on the filter columns would avoid this")
    for m in re.finditer(r"Sort Method: external merge\s+Disk: (\d+)kB", plan):
        findings.append(f"sort spilled {int(m.group(1)) / 1024:.0f} MB to disk; add an index matching ORDER BY or "
                        "raise work_mem")
    for m in re.finditer(r"Nested Loop.*?loops=(\d+)", plan):
        if int(m.group(1)) >= 1000:
            findings.append(f"nested loop executed {int(m.group(1)):,} times")
    total = re.search(r"Execution Time: ([\d.]+) ms", plan)
    summary = f"execution time {float(total.group(1)):,.1f} ms." if total else "no ANALYZE timing present."
    return report("EXPLAIN analysis", findings, summary)


# ── Postgres integration (read-only; tier 3) ─────────────────────────
@contextmanager
def _db() -> Iterator[Any]:
    url = os.environ.get(DB_ENV)
    if not url:
        raise LookupError(f"{DB_ENV} is not set")
    import psycopg

    with psycopg.connect(url, connect_timeout=5, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = 5000")
        try:
            yield conn
        finally:
            conn.rollback()


@tool
def explain_sql_query(query: str) -> str:
    """Run EXPLAIN (estimated plan only, never ANALYZE) for a SELECT query against the configured PostgreSQL
    database (HC_DATABASE_URL) inside a read-only transaction and return the plan text."""
    try:
        tree = _parse(query)
    except ValueError as exc:
        return needs_input("explain_sql_query", str(exc))
    if not isinstance(tree, exp.Select | exp.Union):
        return needs_input("explain_sql_query", "a SELECT statement (only SELECT is explained, for safety)")
    try:
        with _db() as conn:
            rows = conn.execute(f"EXPLAIN (FORMAT TEXT) {tree.sql(dialect='postgres')}").fetchall()
    except LookupError as exc:
        return unavailable("explain_sql_query", str(exc))
    except Exception as exc:
        return unavailable("explain_sql_query", f"database error {type(exc).__name__}: {str(exc).splitlines()[0]}")
    return "EXPLAIN:\n" + "\n".join(r[0] for r in rows)


@tool
def inspect_table_schema(table: str) -> str:
    """Read a table's columns, types, nullability, indexes and estimated row count from the configured
    PostgreSQL database (HC_DATABASE_URL). Accepts 'table' or 'schema.table'."""
    if not _IDENT.match(table.strip()):
        return needs_input("inspect_table_schema", "a plain table name like 'orders' or 'public.orders'")
    schema, _, name = table.strip().rpartition(".")
    schema = schema or "public"
    try:
        with _db() as conn:
            cols = conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (schema, name)).fetchall()
            idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                               (schema, name)).fetchall()
            est = conn.execute("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)",
                               (f"{schema}.{name}",)).fetchone()
    except LookupError as exc:
        return unavailable("inspect_table_schema", str(exc))
    except Exception as exc:
        return unavailable("inspect_table_schema", f"database error {type(exc).__name__}")
    if not cols:
        return f"Table {schema}.{name} not found."
    lines = [f"{c} {t}{'' if n == 'YES' else ' NOT NULL'}" for c, t, n in cols]
    return (f"{schema}.{name} (~{est[0] if est else '?'} rows)\nColumns: " + ", ".join(lines) +
            "\nIndexes:\n" + ("\n".join(i[0] for i in idx) or "none"))


@tool
def get_slow_query_stats(table: str) -> str:
    """Return the slowest statements touching a table from pg_stat_statements on the configured PostgreSQL
    database (HC_DATABASE_URL): mean/total time, calls and cache hit ratio."""
    if not _IDENT.match(table.strip()):
        return needs_input("get_slow_query_stats", "a plain table name")
    sql = ("SELECT left(query, 160), calls, round(mean_exec_time::numeric, 1), round(total_exec_time::numeric), "
           "round(100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0), 1) "
           "FROM pg_stat_statements WHERE query ILIKE %s ORDER BY mean_exec_time DESC LIMIT 5")
    try:
        with _db() as conn:
            rows = conn.execute(sql, (f"%{table.strip()}%",)).fetchall()
    except LookupError as exc:
        return unavailable("get_slow_query_stats", str(exc))
    except Exception as exc:
        return unavailable("get_slow_query_stats", f"pg_stat_statements not readable ({type(exc).__name__})")
    if not rows:
        return f"pg_stat_statements has no statements mentioning '{table}'."
    return "Slowest statements:\n" + "\n".join(
        f"{i}. mean {m} ms, total {t} ms, calls {c}, cache hit {h}% — {q}" for i, (q, c, m, t, h) in enumerate(rows, 1))
