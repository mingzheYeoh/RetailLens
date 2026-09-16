-- RetailLens reconciliation suite.
--
-- Every KPI published in the Power BI report is reproduced here from the raw
-- staging tables. If a number on a dashboard page cannot be re-derived by this
-- file, the number is wrong.
--
--   psql -d retaillens -v ON_ERROR_STOP=1 -f sql/04_reconciliation.sql
--
-- PASS       the check holds
-- KNOWN GAP  the data genuinely disagrees with itself, explained in the note.
--            Left visible on purpose: widening a tolerance until it turns green
--            hides a finding.
-- FAIL       something is broken, stop and fix it.

SET search_path TO olist, public;

WITH raw_totals AS (
    SELECT
        (SELECT count(*) FROM orders)                            AS n_orders,
        (SELECT count(*) FROM order_items)                       AS n_items,
        (SELECT sum(price) FROM order_items)                     AS raw_product_revenue,
        (SELECT sum(price + freight_value) FROM order_items)     AS raw_order_value,
        (SELECT sum(payment_value) FROM order_payments)          AS raw_payment_value
),
mart_totals AS (
    SELECT
        (SELECT count(*) FROM fct_order)                         AS n_fct_orders,
        (SELECT count(DISTINCT order_id) FROM fct_order)         AS n_fct_order_ids,
        (SELECT count(*) FROM fct_order_item)                    AS n_fct_items,
        (SELECT sum(product_revenue) FROM fct_order)             AS mart_product_revenue,
        (SELECT sum(order_value) FROM fct_order)                 AS mart_order_value,
        (SELECT sum(item_value) FROM fct_order_item)             AS mart_item_value,
        (SELECT sum(payment_value) FROM fct_order)               AS mart_payment_value,
        (SELECT count(*) FROM fct_order WHERE payment_value IS NULL)
                                                                 AS orders_without_payment
)
SELECT check_name, status, expected, actual, note FROM (
    SELECT
        1 AS ord,
        'fct_order is one row per order' AS check_name,
        CASE WHEN m.n_fct_orders = m.n_fct_order_ids AND m.n_fct_orders = r.n_orders
             THEN 'PASS' ELSE 'FAIL' END AS status,
        r.n_orders::TEXT   AS expected,
        m.n_fct_orders::TEXT AS actual,
        'Guards against order_items or order_payments fanning out the grain.' AS note
    FROM raw_totals r, mart_totals m

    UNION ALL SELECT
        2,
        'fct_order_item preserves every item line',
        CASE WHEN m.n_fct_items = r.n_items THEN 'PASS' ELSE 'FAIL' END,
        r.n_items::TEXT,
        m.n_fct_items::TEXT,
        'The join back to fct_order must not drop or duplicate item lines.'
    FROM raw_totals r, mart_totals m

    UNION ALL SELECT
        3,
        'product revenue ties to raw item prices',
        CASE WHEN round(m.mart_product_revenue, 2) = round(r.raw_product_revenue, 2)
             THEN 'PASS' ELSE 'FAIL' END,
        round(r.raw_product_revenue, 2)::TEXT,
        round(m.mart_product_revenue, 2)::TEXT,
        'Order-grain revenue must equal the raw SUM(price).'
    FROM raw_totals r, mart_totals m

    UNION ALL SELECT
        4,
        'order value ties to item grain',
        CASE WHEN round(m.mart_order_value, 2) = round(m.mart_item_value, 2)
             THEN 'PASS' ELSE 'FAIL' END,
        round(m.mart_item_value, 2)::TEXT,
        round(m.mart_order_value, 2)::TEXT,
        'The two fact tables must agree on total value.'
    FROM raw_totals r, mart_totals m

    UNION ALL SELECT
        5,
        'payments equal order value',
        CASE WHEN round(r.raw_payment_value, 2) = round(m.mart_order_value, 2)
             THEN 'PASS' ELSE 'KNOWN GAP' END,
        round(m.mart_order_value, 2)::TEXT,
        round(r.raw_payment_value, 2)::TEXT,
        'Payments run ~1% above item value: installment interest and voucher '
        || 'top-ups are charged to the customer but are not item revenue. '
        || 'Sales KPIs therefore use item value, not payment value.'
    FROM raw_totals r, mart_totals m

    UNION ALL SELECT
        6,
        'every order has a payment row',
        CASE WHEN m.orders_without_payment = 0 THEN 'PASS' ELSE 'KNOWN GAP' END,
        '0',
        m.orders_without_payment::TEXT,
        'Missing upstream. Left NULL rather than imputed to zero, so payment-side '
        || 'measures exclude the order instead of understating it.'
    FROM raw_totals r, mart_totals m
) checks
ORDER BY ord;


-- The mistake this model exists to prevent ----------------------------------
-- Joining orders -> items -> payments without collapsing first. Each order is
-- repeated (item lines x payment splits) times, so any SUM over the result is
-- inflated - and by a different factor depending on which column you sum, which
-- is what makes the error hard to spot on a dashboard.
SELECT
    count(*)                                                       AS naive_join_rows,
    (SELECT count(*) FROM orders)                                  AS actual_orders,
    round((SELECT sum(price + freight_value) FROM order_items), 2) AS correct_revenue,
    round(sum(oi.price + oi.freight_value), 2)                     AS naive_join_revenue,
    round(
        100.0 * sum(oi.price + oi.freight_value)
        / (SELECT sum(price + freight_value) FROM order_items) - 100.0
    , 1)                                                           AS pct_revenue_overstated,
    round(
        100.0 * sum(p.payment_value)
        / (SELECT sum(payment_value) FROM order_payments) - 100.0
    , 1)                                                           AS pct_payments_overstated
FROM orders o
JOIN order_items    oi ON oi.order_id = o.order_id
JOIN order_payments p  ON p.order_id  = o.order_id;


-- Headline finding, re-derived from raw tables without touching the marts.
-- Should match reports/tables/review_by_lateness.csv exactly.
SELECT
    o.order_delivered_customer_date::DATE > o.order_estimated_delivery_date::DATE AS is_late,
    count(*)                                                       AS orders,
    round(avg(r.review_score), 3)                                  AS avg_review,
    round(avg(CASE WHEN r.review_score = 1 THEN 1.0 ELSE 0.0 END), 3) AS pct_1_star,
    round(avg(CASE WHEN r.review_score = 5 THEN 1.0 ELSE 0.0 END), 3) AS pct_5_star
FROM orders o
LEFT JOIN v_reviews_deduped r ON r.order_id = o.order_id
WHERE o.order_delivered_customer_date IS NOT NULL
  AND o.order_status NOT IN ('canceled', 'unavailable')
GROUP BY 1
ORDER BY 1;
