-- RetailLens: staging schema for the raw Olist export (PostgreSQL).
--
-- Types are declared explicitly rather than let a loader guess: order timestamps
-- are genuinely nullable (an order that was never delivered has no delivery
-- date), and treating those as 1970-01-01 would quietly corrupt every delivery
-- metric downstream.

DROP SCHEMA IF EXISTS olist CASCADE;
CREATE SCHEMA olist;
SET search_path TO olist, public;

CREATE TABLE customers (
    customer_id              TEXT PRIMARY KEY,
    customer_unique_id       TEXT NOT NULL,   -- one person may hold many customer_ids
    customer_zip_code_prefix TEXT,
    customer_city            TEXT,
    customer_state           CHAR(2)
);

CREATE TABLE sellers (
    seller_id              TEXT PRIMARY KEY,
    seller_zip_code_prefix TEXT,
    seller_city            TEXT,
    seller_state           CHAR(2)
);

CREATE TABLE product_category_translation (
    product_category_name         TEXT PRIMARY KEY,
    product_category_name_english TEXT
);

CREATE TABLE products (
    product_id                 TEXT PRIMARY KEY,
    product_category_name      TEXT,          -- nullable: 623 products have none
    product_name_lenght        INTEGER,       -- [sic] misspelled in the source
    product_description_lenght INTEGER,
    product_photos_qty         INTEGER,
    product_weight_g           NUMERIC,
    product_length_cm          NUMERIC,
    product_height_cm          NUMERIC,
    product_width_cm           NUMERIC
);

CREATE TABLE orders (
    order_id                      TEXT PRIMARY KEY,
    customer_id                   TEXT NOT NULL REFERENCES customers (customer_id),
    order_status                  TEXT NOT NULL,
    order_purchase_timestamp      TIMESTAMP NOT NULL,
    order_approved_at             TIMESTAMP,
    order_delivered_carrier_date  TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,   -- NULL for ~3% never delivered
    order_estimated_delivery_date TIMESTAMP NOT NULL
);

-- Grain: one row per item line. order_item_id is a per-order sequence, so the
-- key is composite. This is the table that fans out order totals.
CREATE TABLE order_items (
    order_id            TEXT NOT NULL REFERENCES orders (order_id),
    order_item_id       INTEGER NOT NULL,
    product_id          TEXT NOT NULL REFERENCES products (product_id),
    seller_id           TEXT NOT NULL REFERENCES sellers (seller_id),
    shipping_limit_date TIMESTAMP,
    price               NUMERIC(12, 2) NOT NULL,
    freight_value       NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

-- Grain: one row per payment split. An order paid half on card and half on a
-- voucher has two rows. This table fans out order totals a second time.
CREATE TABLE order_payments (
    order_id             TEXT NOT NULL REFERENCES orders (order_id),
    payment_sequential   INTEGER NOT NULL,
    payment_type         TEXT,
    payment_installments INTEGER,
    payment_value        NUMERIC(12, 2),
    PRIMARY KEY (order_id, payment_sequential)
);

-- No primary key on purpose: the published file contains duplicate review_ids
-- and a handful of orders with more than one review. Deduplication is an
-- explicit modelling decision, made in 03_marts.sql, not hidden in a constraint
-- that would reject the load.
CREATE TABLE order_reviews (
    review_id              TEXT,
    order_id               TEXT NOT NULL,
    review_score           SMALLINT CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title   TEXT,
    review_comment_message TEXT,
    review_creation_date   TIMESTAMP,
    review_answer_timestamp TIMESTAMP
);

CREATE INDEX idx_order_items_order  ON order_items (order_id);
CREATE INDEX idx_order_items_product ON order_items (product_id);
CREATE INDEX idx_payments_order     ON order_payments (order_id);
CREATE INDEX idx_reviews_order      ON order_reviews (order_id);
CREATE INDEX idx_orders_purchased   ON orders (order_purchase_timestamp);
