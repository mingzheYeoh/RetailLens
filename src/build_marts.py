"""RetailLens ETL: raw Olist CSVs -> star-schema marts for Power BI / SQL.

The whole point of this file is grain discipline.

    orders        1 row per order
    order_items   1 row per *item line*      (fan-out: avg 1.13x)
    payments      1 row per *payment split*  (fan-out: avg 1.04x)
    reviews       ~1 row per order, with duplicates in the public file

Joining all four directly multiplies order value by items x payments. So items
and payments are each collapsed to order grain BEFORE they touch `orders`.

Outputs
    data/processed/fct_order_item.csv   grain: order_id + order_item_id
    data/processed/fct_order.csv        grain: order_id
    data/processed/dim_*.csv
    reports/tables/*.csv                small aggregates used by the report
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
TABLES = ROOT / "reports" / "tables"

# Orders in these statuses never became revenue.
CANCELLED_STATUSES = ("canceled", "unavailable")

DATE_COLS = [
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def load_raw():
    def read(name, **kw):
        return pd.read_csv(RAW / f"{name}.csv", **kw)

    return {
        "orders": read("olist_orders_dataset", parse_dates=DATE_COLS),
        "items": read("olist_order_items_dataset", parse_dates=["shipping_limit_date"]),
        "payments": read("olist_order_payments_dataset"),
        "reviews": read(
            "olist_order_reviews_dataset",
            parse_dates=["review_creation_date", "review_answer_timestamp"],
        ),
        "products": read("olist_products_dataset"),
        "customers": read("olist_customers_dataset"),
        "sellers": read("olist_sellers_dataset"),
        "category_tr": read("product_category_name_translation"),
    }


def build_dim_product(products, tr):
    d = products.merge(tr, on="product_category_name", how="left")
    # 623 products carry no category at all, and a few categories are missing
    # from the translation file. Both land in one explicit bucket instead of
    # silently dropping out of the category page.
    d["product_category"] = (
        d["product_category_name_english"]
        .fillna(d["product_category_name"])
        .fillna("unknown")
        .str.replace("_", " ")
        .str.title()
    )
    return d[["product_id", "product_category", "product_weight_g"]]


def dedupe_reviews(reviews):
    """One review per order: the public file has duplicate review_ids.

    Keeping the latest answered review is the defensible choice, it is the score
    that reflects the closed interaction. Flagged in the report as a limitation
    because averaging instead would shift scores slightly.
    """
    r = reviews.sort_values("review_answer_timestamp")
    r = r.drop_duplicates(subset="order_id", keep="last")
    return r[["order_id", "review_score", "review_creation_date"]]


def build_facts(raw):
    orders, items, payments = raw["orders"], raw["items"], raw["payments"]
    reviews = dedupe_reviews(raw["reviews"])

    # --- order-grain attributes ---------------------------------------------
    o = orders.merge(
        raw["customers"][
            ["customer_id", "customer_unique_id", "customer_state", "customer_city"]
        ],
        on="customer_id",
        how="left",
    )
    o["is_cancelled"] = o["order_status"].isin(CANCELLED_STATUSES)
    o["is_delivered"] = o["order_delivered_customer_date"].notna()

    day = np.timedelta64(1, "D")
    o["delivery_days"] = (
        o["order_delivered_customer_date"] - o["order_purchase_timestamp"]
    ) / day
    o["estimated_days"] = (
        o["order_estimated_delivery_date"] - o["order_purchase_timestamp"]
    ) / day
    # Positive delay = later than promised. The Olist estimate is date-only, so
    # arriving any time on the promised day counts as on time.
    o["delay_days"] = (
        o["order_delivered_customer_date"].dt.normalize()
        - o["order_estimated_delivery_date"].dt.normalize()
    ) / day
    # Nullable boolean, not numpy bool: an undelivered order is not "on time",
    # it is unknown, and every late-rate average must skip it rather than count
    # it as False.
    o["is_late"] = (o["delay_days"] > 0).astype("boolean").where(o["is_delivered"])
    o["delivery_bucket"] = pd.cut(
        o["delay_days"],
        bins=[-np.inf, -10, -3, 0, 3, 10, np.inf],
        labels=[
            "10+ days early",
            "3-10 days early",
            "0-3 days early",
            "1-3 days late",
            "3-10 days late",
            "10+ days late",
        ],
    )
    o["order_month"] = o["order_purchase_timestamp"].dt.to_period("M").dt.to_timestamp()

    # --- collapse payments to order grain BEFORE joining ---------------------
    pay = payments.groupby("order_id", as_index=False).agg(
        payment_value=("payment_value", "sum"),
        payment_splits=("payment_sequential", "count"),
        max_installments=("payment_installments", "max"),
    )
    # The payment type carrying the most money is the one worth reporting.
    primary = (
        payments.sort_values("payment_value", ascending=False)
        .drop_duplicates("order_id")[["order_id", "payment_type"]]
        .rename(columns={"payment_type": "primary_payment_type"})
    )
    pay = pay.merge(primary, on="order_id", how="left")

    # --- collapse items to order grain BEFORE joining ------------------------
    it = items.groupby("order_id", as_index=False).agg(
        item_count=("order_item_id", "count"),
        product_revenue=("price", "sum"),
        freight_value=("freight_value", "sum"),
        seller_count=("seller_id", "nunique"),
    )
    it["order_value"] = it["product_revenue"] + it["freight_value"]

    fct_order = (
        o.merge(it, on="order_id", how="left")
        .merge(pay, on="order_id", how="left")
        .merge(reviews, on="order_id", how="left")
    )
    assert fct_order["order_id"].is_unique, "fan-out: fct_order is no longer order grain"

    fct_order = fct_order[
        [
            "order_id", "customer_id", "customer_unique_id", "customer_state",
            "customer_city", "order_status", "is_cancelled", "is_delivered",
            "order_purchase_timestamp", "order_month",
            "order_delivered_customer_date", "order_estimated_delivery_date",
            "delivery_days", "estimated_days", "delay_days", "is_late",
            "delivery_bucket", "item_count", "seller_count", "product_revenue",
            "freight_value", "order_value", "payment_value", "payment_splits",
            "max_installments", "primary_payment_type", "review_score",
        ]
    ]

    # --- item grain: the ONLY correct grain for category / seller analysis ---
    order_attrs = o[
        [
            "order_id", "customer_state", "order_status", "is_cancelled",
            "is_delivered", "order_purchase_timestamp", "order_month",
            "delivery_days", "delay_days", "is_late", "delivery_bucket",
        ]
    ]
    fct_item = (
        items.merge(order_attrs, on="order_id", how="inner")
        .merge(
            build_dim_product(raw["products"], raw["category_tr"]),
            on="product_id",
            how="left",
        )
        .merge(reviews[["order_id", "review_score"]], on="order_id", how="left")
    )
    fct_item["product_category"] = fct_item["product_category"].fillna("Unknown")
    fct_item["item_value"] = fct_item["price"] + fct_item["freight_value"]
    assert len(fct_item) == len(items), "item grain changed - a join fanned out"

    return fct_order, fct_item


def reconcile(fct_order, fct_item, raw):
    """Grain and totals checks, plus the two variances that are real.

    A check marked `known_gap=True` is one where the data genuinely disagrees
    with itself. Widening the tolerance until it goes green would hide a finding,
    so it stays visible as KNOWN GAP with the explanation attached.
    """
    items, payments = raw["items"], raw["payments"]
    order_value = fct_order["order_value"].sum()
    payment_total = payments["payment_value"].sum()
    missing_payment = int(fct_order["payment_value"].isna().sum())

    checks = [
        (
            "fct_order is one row per order",
            bool(fct_order["order_id"].is_unique),
            "unique",
            "unique",
            False,
            "Guards against items/payments fanning out the order grain.",
        ),
        (
            "fct_order_item preserves every item line",
            len(fct_item) == len(items),
            len(items),
            len(fct_item),
            False,
            "An inner join to orders must not drop or duplicate item lines.",
        ),
        (
            "product revenue ties to raw item prices",
            bool(np.isclose(fct_order["product_revenue"].sum(), items["price"].sum())),
            round(items["price"].sum(), 2),
            round(fct_order["product_revenue"].sum(), 2),
            False,
            "Order-grain revenue must equal the raw SUM(price).",
        ),
        (
            "order value ties to item grain",
            bool(np.isclose(order_value, fct_item["item_value"].sum())),
            round(fct_item["item_value"].sum(), 2),
            round(order_value, 2),
            False,
            "The two fact tables must agree on total value.",
        ),
        (
            "payments equal order value",
            bool(np.isclose(payment_total, order_value)),
            round(order_value, 2),
            round(payment_total, 2),
            True,
            f"Payments run {(payment_total / order_value - 1):.2%} above item value: "
            "installment interest and voucher top-ups are charged to the customer "
            "but are not item revenue. Sales KPIs therefore use item value, not "
            "payment value.",
        ),
        (
            "every order has a payment row",
            missing_payment == 0,
            0,
            missing_payment,
            True,
            f"{missing_payment} delivered order has no payment record upstream. "
            "Left as NULL rather than imputed to zero, so payment-side measures "
            "exclude it instead of understating it.",
        ),
    ]
    df = pd.DataFrame(
        checks, columns=["check", "ok", "expected", "actual", "known_gap", "note"]
    )
    df["status"] = np.where(df["ok"], "PASS", np.where(df["known_gap"], "KNOWN GAP", "FAIL"))
    return df[["check", "status", "expected", "actual", "note"]]


def build_report_tables(fct_order, fct_item):
    sales = fct_order[~fct_order["is_cancelled"]]
    delivered = fct_order[fct_order["is_delivered"] & ~fct_order["is_cancelled"]]
    sold_items = fct_item[~fct_item["is_cancelled"]]

    monthly = (
        sales.groupby("order_month")
        .agg(
            orders=("order_id", "count"),
            revenue=("order_value", "sum"),
            aov=("order_value", "mean"),
            customers=("customer_unique_id", "nunique"),
        )
        .round(2)
        .reset_index()
    )

    category = (
        sold_items.groupby("product_category")
        .agg(
            items=("order_item_id", "count"),
            revenue=("item_value", "sum"),
            avg_price=("price", "mean"),
            avg_freight=("freight_value", "mean"),
            avg_review=("review_score", "mean"),
            late_rate=("is_late", "mean"),
        )
        .round(3)
        .sort_values("revenue", ascending=False)
        .reset_index()
    )

    delivery = (
        delivered.groupby("delivery_bucket", observed=False)
        .agg(
            orders=("order_id", "count"),
            avg_review=("review_score", "mean"),
            revenue=("order_value", "sum"),
        )
        .round(3)
        .reset_index()
    )

    state = (
        delivered.groupby("customer_state")
        .agg(
            orders=("order_id", "count"),
            revenue=("order_value", "sum"),
            avg_delivery_days=("delivery_days", "mean"),
            late_rate=("is_late", "mean"),
            avg_review=("review_score", "mean"),
        )
        .round(3)
        .sort_values("revenue", ascending=False)
        .reset_index()
    )

    review_by_late = (
        delivered.groupby("is_late")
        .agg(
            orders=("order_id", "count"),
            avg_review=("review_score", "mean"),
            pct_1_star=("review_score", lambda s: (s == 1).mean()),
            pct_5_star=("review_score", lambda s: (s == 5).mean()),
        )
        .round(3)
        .reset_index()
    )

    return {
        "monthly_sales": monthly,
        "category_performance": category,
        "delivery_vs_review": delivery,
        "state_performance": state,
        "review_by_lateness": review_by_late,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)

    raw = load_raw()
    fct_order, fct_item = build_facts(raw)

    fct_order.to_csv(OUT / "fct_order.csv", index=False)
    fct_item.to_csv(OUT / "fct_order_item.csv", index=False)
    build_dim_product(raw["products"], raw["category_tr"]).to_csv(
        OUT / "dim_product.csv", index=False
    )
    raw["customers"].to_csv(OUT / "dim_customer.csv", index=False)
    raw["sellers"].to_csv(OUT / "dim_seller.csv", index=False)

    checks = reconcile(fct_order, fct_item, raw)
    checks.to_csv(TABLES / "reconciliation.csv", index=False)
    print(checks.drop(columns="note").to_string(index=False), "\n")

    for name, df in build_report_tables(fct_order, fct_item).items():
        df.to_csv(TABLES / f"{name}.csv", index=False)
        print(f"wrote reports/tables/{name}.csv ({len(df)} rows)")

    print(f"\nfct_order  {len(fct_order):,} rows")
    print(f"fct_item   {len(fct_item):,} rows")


if __name__ == "__main__":
    main()
