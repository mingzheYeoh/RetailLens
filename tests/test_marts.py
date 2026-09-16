"""Grain and delivery-logic checks on a hand-built 4-order fixture.

Runs without the 65 MB download, in under a second:

    python tests/test_marts.py

The fixture is deliberately nasty - it contains every shape that breaks a naive
join: a multi-item order, a split payment, an order that is both, an order that
never arrived, a same-day arrival, and a duplicate review.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_marts import build_facts  # noqa: E402

TS = pd.Timestamp


def fixture():
    """4 orders: 6 item lines, 6 payment splits, 5 review rows.

    A naive orders-items-payments join would produce 10 rows and would report
    total order value as 1,020 instead of the true 600.
    """
    orders = pd.DataFrame([
        # 3 items, 1 payment. Delivered 2 days EARLY.
        ("o1", "c1", "delivered", TS("2017-01-01"), TS("2017-01-08 09:00"), TS("2017-01-10")),
        # 1 item, 3 payment splits. Delivered at 23:00 ON the promised day: on time.
        ("o2", "c2", "delivered", TS("2017-02-01"), TS("2017-02-10 23:00"), TS("2017-02-10")),
        # 2 items, 2 payments. Delivered 1 day LATE.
        ("o3", "c3", "delivered", TS("2017-03-01"), TS("2017-03-12 01:00"), TS("2017-03-11")),
        # Never delivered, and cancelled.
        ("o4", "c4", "canceled", TS("2017-04-01"), pd.NaT, TS("2017-04-10")),
    ], columns=["order_id", "customer_id", "order_status", "order_purchase_timestamp",
                "order_delivered_customer_date", "order_estimated_delivery_date"])
    orders["order_approved_at"] = orders["order_purchase_timestamp"]
    orders["order_delivered_carrier_date"] = orders["order_purchase_timestamp"]

    items = pd.DataFrame([
        ("o1", 1, "p1", "s1", 100.0, 10.0), ("o1", 2, "p1", "s1", 50.0, 5.0),
        ("o1", 3, "p2", "s2", 30.0, 5.0),
        ("o2", 1, "p2", "s2", 200.0, 20.0),
        ("o3", 1, "p1", "s1", 60.0, 10.0), ("o3", 2, "p3", "s1", 100.0, 10.0),
    ], columns=["order_id", "order_item_id", "product_id", "seller_id", "price",
                "freight_value"])
    items["shipping_limit_date"] = TS("2017-01-05")

    payments = pd.DataFrame([
        ("o1", 1, "credit_card", 3, 200.0),
        ("o2", 1, "voucher", 1, 20.0), ("o2", 2, "credit_card", 6, 180.0),
        ("o2", 3, "voucher", 1, 30.0),
        ("o3", 1, "boleto", 1, 100.0), ("o3", 2, "credit_card", 2, 80.0),
    ], columns=["order_id", "payment_sequential", "payment_type",
                "payment_installments", "payment_value"])

    reviews = pd.DataFrame([
        ("r1", "o1", 5, TS("2017-01-11"), TS("2017-01-12")),
        # Two reviews for o2. The later-answered 2 must win over the 4.
        ("r2", "o2", 4, TS("2017-02-11"), TS("2017-02-12")),
        ("r3", "o2", 2, TS("2017-02-11"), TS("2017-02-20")),
        ("r4", "o3", 1, TS("2017-03-13"), TS("2017-03-14")),
        ("r5", "o4", 3, TS("2017-04-11"), TS("2017-04-12")),
    ], columns=["review_id", "order_id", "review_score", "review_creation_date",
                "review_answer_timestamp"])

    products = pd.DataFrame([
        ("p1", "cama_mesa_banho", 500.0), ("p2", "beleza_saude", 200.0),
        ("p3", None, 100.0),  # no category at all
    ], columns=["product_id", "product_category_name", "product_weight_g"])

    customers = pd.DataFrame({
        "customer_id": ["c1", "c2", "c3", "c4"],
        "customer_unique_id": ["u1", "u2", "u2", "u4"],  # u2 ordered twice
        "customer_state": ["SP", "RJ", "SP", "MG"],
        "customer_city": ["sao paulo", "rio", "campinas", "bh"],
    })

    return {
        "orders": orders, "items": items, "payments": payments, "reviews": reviews,
        "products": products, "customers": customers,
        "sellers": pd.DataFrame({"seller_id": ["s1", "s2"]}),
        "category_tr": pd.DataFrame({
            "product_category_name": ["cama_mesa_banho", "beleza_saude"],
            "product_category_name_english": ["bed_bath_table", "health_beauty"],
        }),
    }


def test_grain_is_preserved(order, item):
    assert len(order) == 4, f"expected 4 orders, got {len(order)}"
    assert order["order_id"].is_unique, "payments or items fanned out the order grain"
    assert len(item) == 6, f"expected 6 item lines, got {len(item)}"


def test_no_double_counting(order, item):
    # The whole point: 600, not the 1,020 a naive three-way join would report.
    assert order["order_value"].sum() == 600.0, order["order_value"].sum()
    assert item["item_value"].sum() == 600.0, item["item_value"].sum()
    o1 = order.set_index("order_id").loc["o1"]
    assert o1["item_count"] == 3 and o1["order_value"] == 200.0
    assert o1["seller_count"] == 2, "seller_count must be distinct sellers, not rows"


def test_payments_collapse_without_multiplying_items(order):
    o2 = order.set_index("order_id").loc["o2"]
    assert o2["payment_value"] == 230.0, "3 splits must sum, not repeat the order"
    assert o2["payment_splits"] == 3
    assert o2["max_installments"] == 6
    # Primary type is the split carrying the most money, not the first row.
    assert o2["primary_payment_type"] == "credit_card"


def test_lateness_is_measured_in_whole_days(order):
    o = order.set_index("order_id")
    # Arrived 23:00 on the promised DAY. A timestamp comparison would call this
    # 0.96 days late; the promise is a day, so it is on time.
    assert o.loc["o2", "is_late"] is False or o.loc["o2", "is_late"] == False  # noqa: E712
    assert o.loc["o2", "delay_days"] == 0
    assert o.loc["o1", "is_late"] == False and o.loc["o1", "delay_days"] == -2  # noqa: E712
    assert o.loc["o3", "is_late"] == True and o.loc["o3", "delay_days"] == 1  # noqa: E712
    # Never delivered: unknown, not "on time". Must not average in as False.
    assert pd.isna(o.loc["o4", "is_late"]), "undelivered order must be NA, not False"
    assert order["is_late"].mean() == 1 / 3, "late rate must skip the undelivered order"


def test_delivery_buckets(order):
    o = order.set_index("order_id")
    assert o.loc["o1", "delivery_bucket"] == "0-3 days early"   # -2 days
    assert o.loc["o2", "delivery_bucket"] == "0-3 days early"   # exactly 0
    assert o.loc["o3", "delivery_bucket"] == "1-3 days late"    # +1 day
    assert pd.isna(o.loc["o4", "delivery_bucket"])


def test_reviews_deduped_to_latest_answer(order):
    o = order.set_index("order_id")
    assert o.loc["o2", "review_score"] == 2, "must keep the latest-answered review"
    assert len(order) == 4, "a duplicate review must not duplicate the order"


def test_missing_category_becomes_unknown(item):
    p3 = item[item["product_id"] == "p3"]
    assert (p3["product_category"] == "Unknown").all()
    assert item["product_category"].isna().sum() == 0
    # Category revenue must still tie back to the order-grain total.
    assert item.groupby("product_category")["item_value"].sum().sum() == 600.0


def test_cancelled_flagged_not_dropped(order):
    o = order.set_index("order_id")
    assert o.loc["o4", "is_cancelled"] == True  # noqa: E712
    assert o.loc[["o1", "o2", "o3"], "is_cancelled"].sum() == 0
    assert len(order) == 4, "cancelled orders stay in the fact, filtered by measures"


def main():
    order, item = build_facts(fixture())
    checks = [
        ("grain is preserved", lambda: test_grain_is_preserved(order, item)),
        ("no double counting", lambda: test_no_double_counting(order, item)),
        ("payments collapse", lambda: test_payments_collapse_without_multiplying_items(order)),
        ("lateness in whole days", lambda: test_lateness_is_measured_in_whole_days(order)),
        ("delivery buckets", lambda: test_delivery_buckets(order)),
        ("reviews deduped", lambda: test_reviews_deduped_to_latest_answer(order)),
        ("missing category", lambda: test_missing_category_becomes_unknown(item)),
        ("cancelled flagged", lambda: test_cancelled_flagged_not_dropped(order)),
    ]
    for name, check in checks:
        check()
        print(f"  pass  {name}")
    print(f"\n{len(checks)} checks passed")


if __name__ == "__main__":
    main()
