"""Run sql/03_marts.sql and sql/04_reconciliation.sql, then prove the SQL and the
pandas pipeline agree.

The marts exist twice on purpose: once in pandas (src/build_marts.py, feeding the
Power BI model) and once in SQL (sql/03_marts.sql, the warehouse definition). Two
implementations of the same KPI are only useful if something checks they match,
which is what this file does.

PostgreSQL is the target engine. DuckDB runs the same files locally so the checks
work in CI without a server. Only one shim is needed: DuckDB has no initcap().
"""

import re
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SQL = ROOT / "sql"
TABLES = ROOT / "reports" / "tables"

STAGING = {
    "customers": "olist_customers_dataset",
    "sellers": "olist_sellers_dataset",
    "product_category_translation": "product_category_name_translation",
    "products": "olist_products_dataset",
    "orders": "olist_orders_dataset",
    "order_items": "olist_order_items_dataset",
    "order_payments": "olist_order_payments_dataset",
    "order_reviews": "olist_order_reviews_dataset",
}

# Postgres has initcap built in; DuckDB does not. Same semantics, so the .sql
# files stay portable rather than carrying an engine branch.
INITCAP_SHIM = """
CREATE OR REPLACE MACRO initcap(s) AS
    array_to_string(
        list_transform(
            string_split(lower(s), ' '),
            w -> CASE WHEN length(w) = 0 THEN w ELSE upper(w[1]) || w[2:] END
        ), ' ');
"""


def build_staging(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS olist; SET search_path TO 'olist';")
    con.execute(INITCAP_SHIM)
    for table, csv_name in STAGING.items():
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT * FROM read_csv_auto('{(RAW / (csv_name + '.csv')).as_posix()}')"
        )


def run_script(con, filename):
    """Execute a .sql file and return the result of every SELECT in it.

    Statements are split by the real SQL parser, not by naive semicolon
    splitting, which would cut a statement in half at a semicolon inside a
    comment or a string literal. The only line dropped is the psql-only
    `SET search_path`.
    """
    text = (SQL / filename).read_text(encoding="utf-8")
    text = re.sub(r"(?im)^\s*SET\s+search_path[^;]*;", "", text)
    results = []
    for stmt in duckdb.extract_statements(text):
        if stmt.type == duckdb.StatementType.SELECT:
            results.append(con.sql(stmt.query).df())
        else:
            con.execute(stmt.query)
    return results


def compare(name, sql_df, csv_name, keys, values, tol=0.011):
    """Assert the SQL view and the pandas table agree row for row."""
    pandas_df = pd.read_csv(TABLES / f"{csv_name}.csv")
    left = sql_df.sort_values(keys).reset_index(drop=True)
    right = pandas_df.sort_values(keys).reset_index(drop=True)

    assert len(left) == len(right), f"{name}: {len(left)} SQL rows vs {len(right)} pandas rows"
    for col in values:
        delta = (left[col].astype(float) - right[col].astype(float)).abs().max()
        assert delta <= tol, f"{name}.{col} differs by up to {delta}"
    print(f"  match  {name:22s} {len(left):>3} rows, {len(values)} measures")


def main():
    con = duckdb.connect()
    build_staging(con)
    run_script(con, "03_marts.sql")

    checks, naive_join, headline = run_script(con, "04_reconciliation.sql")

    print("SQL reconciliation (sql/04_reconciliation.sql)\n")
    print(checks.drop(columns="note").to_string(index=False))
    print("\nnotes:")
    for _, row in checks[checks["status"] != "PASS"].iterrows():
        print(f"  {row['check_name']}: {row['note']}")

    n = naive_join.iloc[0]
    print(
        f"\nNaive orders-items-payments join: {n['naive_join_rows']:,.0f} rows for "
        f"{n['actual_orders']:,.0f} orders"
        f"\n  revenue  {n['naive_join_revenue']:,.2f} vs {n['correct_revenue']:,.2f} correct"
        f"  ({n['pct_revenue_overstated']:+.1f}%)"
        f"\n  payments overstated by {n['pct_payments_overstated']:+.1f}%"
    )

    print("\nSQL vs pandas, same KPI computed twice:")
    compare(
        "monthly sales",
        con.sql("SELECT * FROM v_monthly_sales").df().assign(
            order_month=lambda d: d["order_month"].dt.strftime("%Y-%m-%d")
        ),
        "monthly_sales",
        ["order_month"],
        ["orders", "revenue", "aov", "customers"],
    )
    compare(
        "category performance",
        con.sql("SELECT * FROM v_category_performance").df(),
        "category_performance",
        ["product_category"],
        ["items", "revenue", "avg_price", "avg_freight", "avg_review", "late_rate"],
    )
    compare(
        "state performance",
        con.sql("SELECT * FROM v_state_performance").df(),
        "state_performance",
        ["customer_state"],
        ["orders", "revenue", "avg_delivery_days", "late_rate", "avg_review"],
    )
    compare(
        "delivery vs review",
        con.sql("SELECT * FROM v_delivery_vs_review").df(),
        "delivery_vs_review",
        ["delivery_bucket"],
        ["orders", "avg_review", "revenue"],
    )

    print("\nHeadline finding, re-derived straight from the raw tables:")
    print(headline.to_string(index=False))

    failed = checks[checks["status"] == "FAIL"]
    if len(failed):
        raise SystemExit(f"\n{len(failed)} reconciliation check(s) FAILED")
    print("\nAll checks accounted for: no FAILs.")


if __name__ == "__main__":
    main()
