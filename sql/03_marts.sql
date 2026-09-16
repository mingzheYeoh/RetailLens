-- RetailLens marts: the same star schema the Power BI model loads.
--
-- Reading order, because the grain logic is the point of this file:
--   1. v_reviews_deduped      one review per order
--   2. v_items_by_order       item lines collapsed to order grain
--   3. v_payments_by_order    payment splits collapsed to order grain
--   4. fct_order              orders + the three collapsed views  (order grain)
--   5. fct_order_item         item lines + order attributes       (item grain)
--
-- Steps 2 and 3 exist so that step 4 is a pure 1:1 join. Joining orders to
-- order_items and order_payments directly repeats each order (item lines x
-- payment splits) times: 117,601 rows instead of 99,441 orders. Measured on this
-- dataset by the last query in 04_reconciliation.sql, that overstates item
-- revenue by 4.6% and payment value by 26.9%.

SET search_path TO olist, public;

-- 1 ------------------------------------------------------------------------
-- The published reviews file has duplicate review_ids and a few orders with
-- more than one review. Keep the most recently answered one per order.
CREATE OR REPLACE VIEW v_reviews_deduped AS
SELECT order_id, review_score, review_creation_date
FROM (
    SELECT
        order_id,
        review_score,
        review_creation_date,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY review_answer_timestamp DESC NULLS LAST
        ) AS rn
    FROM order_reviews
) ranked
WHERE rn = 1;

-- 2 ------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_items_by_order AS
SELECT
    order_id,
    count(*)                        AS item_count,
    count(DISTINCT seller_id)       AS seller_count,
    sum(price)                      AS product_revenue,
    sum(freight_value)              AS freight_value,
    sum(price + freight_value)      AS order_value
FROM order_items
GROUP BY order_id;

-- 3 ------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_payments_by_order AS
SELECT
    p.order_id,
    sum(p.payment_value)        AS payment_value,
    count(*)                    AS payment_splits,
    max(p.payment_installments) AS max_installments,
    -- The split carrying the most money is the payment type worth reporting.
    (
        SELECT p2.payment_type
        FROM order_payments p2
        WHERE p2.order_id = p.order_id
        ORDER BY p2.payment_value DESC, p2.payment_sequential
        LIMIT 1
    ) AS primary_payment_type
FROM order_payments p
GROUP BY p.order_id;

-- 4 ------------------------------------------------------------------------
-- Grain: one row per order. All three joins below are 1:1 by construction.
CREATE OR REPLACE VIEW fct_order AS
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    c.customer_state,
    c.customer_city,
    o.order_status,
    o.order_status IN ('canceled', 'unavailable')      AS is_cancelled,
    o.order_delivered_customer_date IS NOT NULL        AS is_delivered,
    o.order_purchase_timestamp,
    DATE_TRUNC('month', o.order_purchase_timestamp)    AS order_month,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,

    EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp))
        / 86400.0                                      AS delivery_days,
    EXTRACT(EPOCH FROM (o.order_estimated_delivery_date - o.order_purchase_timestamp))
        / 86400.0                                      AS estimated_days,
    -- Both sides truncated to a date: the promise is a day, not an instant, so
    -- arriving at 23:00 on the promised day is on time.
    (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE)
                                                       AS delay_days,
    CASE
        WHEN o.order_delivered_customer_date IS NULL THEN NULL
        ELSE (o.order_delivered_customer_date::DATE > o.order_estimated_delivery_date::DATE)
    END                                                AS is_late,
    CASE
        WHEN o.order_delivered_customer_date IS NULL THEN NULL
        WHEN (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE) <= -10
            THEN '10+ days early'
        WHEN (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE) <= -3
            THEN '3-10 days early'
        WHEN (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE) <= 0
            THEN '0-3 days early'
        WHEN (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE) <= 3
            THEN '1-3 days late'
        WHEN (o.order_delivered_customer_date::DATE - o.order_estimated_delivery_date::DATE) <= 10
            THEN '3-10 days late'
        ELSE '10+ days late'
    END                                                AS delivery_bucket,

    i.item_count,
    i.seller_count,
    i.product_revenue,
    i.freight_value,
    i.order_value,
    p.payment_value,
    p.payment_splits,
    p.max_installments,
    p.primary_payment_type,
    r.review_score
FROM orders o
JOIN customers          c ON c.customer_id = o.customer_id
LEFT JOIN v_items_by_order    i ON i.order_id = o.order_id
LEFT JOIN v_payments_by_order p ON p.order_id = o.order_id
LEFT JOIN v_reviews_deduped   r ON r.order_id = o.order_id;

-- 5 ------------------------------------------------------------------------
-- Grain: one row per item line. This is the only correct grain for category and
-- seller analysis. Never sum payment_value here.
CREATE OR REPLACE VIEW fct_order_item AS
SELECT
    oi.order_id,
    oi.order_item_id,
    oi.product_id,
    oi.seller_id,
    COALESCE(
        initcap(replace(COALESCE(t.product_category_name_english,
                                 pr.product_category_name), '_', ' ')),
        'Unknown'
    )                               AS product_category,
    oi.price,
    oi.freight_value,
    oi.price + oi.freight_value     AS item_value,
    f.customer_state,
    f.order_status,
    f.is_cancelled,
    f.is_delivered,
    f.order_purchase_timestamp,
    f.order_month,
    f.delivery_days,
    f.delay_days,
    f.is_late,
    f.delivery_bucket,
    f.review_score
FROM order_items oi
JOIN fct_order f  ON f.order_id = oi.order_id
LEFT JOIN products pr ON pr.product_id = oi.product_id
LEFT JOIN product_category_translation t
       ON t.product_category_name = pr.product_category_name;

-- Reporting views ----------------------------------------------------------
-- Sales KPIs exclude cancelled and unavailable orders; delivery KPIs further
-- require an actual delivery date. See powerbi/KPI_DEFINITIONS.md.

CREATE OR REPLACE VIEW v_monthly_sales AS
SELECT
    order_month,
    count(*)                            AS orders,
    round(sum(order_value), 2)          AS revenue,
    round(avg(order_value), 2)          AS aov,
    count(DISTINCT customer_unique_id)  AS customers
FROM fct_order
WHERE NOT is_cancelled
GROUP BY order_month
ORDER BY order_month;

CREATE OR REPLACE VIEW v_category_performance AS
SELECT
    product_category,
    count(*)                                        AS items,
    round(sum(item_value), 2)                       AS revenue,
    round(avg(price), 3)                            AS avg_price,
    round(avg(freight_value), 3)                    AS avg_freight,
    round(avg(review_score), 3)                     AS avg_review,
    round(avg(CASE WHEN is_late THEN 1.0 ELSE 0.0 END), 3) AS late_rate
FROM fct_order_item
WHERE NOT is_cancelled
GROUP BY product_category
ORDER BY revenue DESC;

CREATE OR REPLACE VIEW v_delivery_vs_review AS
SELECT
    delivery_bucket,
    count(*)                    AS orders,
    round(avg(review_score), 3) AS avg_review,
    round(sum(order_value), 2)  AS revenue
FROM fct_order
WHERE is_delivered AND NOT is_cancelled
GROUP BY delivery_bucket;

CREATE OR REPLACE VIEW v_state_performance AS
SELECT
    customer_state,
    count(*)                                        AS orders,
    round(sum(order_value), 2)                      AS revenue,
    round(avg(delivery_days), 3)                    AS avg_delivery_days,
    round(avg(CASE WHEN is_late THEN 1.0 ELSE 0.0 END), 3) AS late_rate,
    round(avg(review_score), 3)                     AS avg_review
FROM fct_order
WHERE is_delivered AND NOT is_cancelled
GROUP BY customer_state
ORDER BY revenue DESC;

CREATE OR REPLACE VIEW v_review_by_lateness AS
SELECT
    is_late,
    count(*)                                             AS orders,
    round(avg(review_score), 3)                          AS avg_review,
    round(avg(CASE WHEN review_score = 1 THEN 1.0 ELSE 0.0 END), 3) AS pct_1_star,
    round(avg(CASE WHEN review_score = 5 THEN 1.0 ELSE 0.0 END), 3) AS pct_5_star
FROM fct_order
WHERE is_delivered AND NOT is_cancelled
GROUP BY is_late;
