-- Load the raw CSVs into the staging schema.
--
-- Run from the repository root with psql, which resolves the relative paths:
--     psql -d retaillens -v ON_ERROR_STOP=1 -f sql/01_schema.sql
--     psql -d retaillens -v ON_ERROR_STOP=1 -f sql/02_load.sql
--
-- Load order matters: the foreign keys in 01_schema.sql mean dimensions have to
-- land before the facts that reference them.

SET search_path TO olist, public;

\copy customers                    FROM 'data/raw/olist_customers_dataset.csv'             WITH (FORMAT csv, HEADER true)
\copy sellers                      FROM 'data/raw/olist_sellers_dataset.csv'               WITH (FORMAT csv, HEADER true)
\copy product_category_translation FROM 'data/raw/product_category_name_translation.csv'   WITH (FORMAT csv, HEADER true)
\copy products                     FROM 'data/raw/olist_products_dataset.csv'              WITH (FORMAT csv, HEADER true)
\copy orders                       FROM 'data/raw/olist_orders_dataset.csv'                WITH (FORMAT csv, HEADER true)
\copy order_items                  FROM 'data/raw/olist_order_items_dataset.csv'           WITH (FORMAT csv, HEADER true)
\copy order_payments               FROM 'data/raw/olist_order_payments_dataset.csv'        WITH (FORMAT csv, HEADER true)
\copy order_reviews                FROM 'data/raw/olist_order_reviews_dataset.csv'         WITH (FORMAT csv, HEADER true)

ANALYZE;

-- Expected row counts, as a first smoke test:
--   customers 99,441 | sellers 3,095 | products 32,951 | orders     99,441
--   items    112,650 | payments 103,886 | reviews 100,000
SELECT 'orders' AS table_name, count(*) FROM orders
UNION ALL SELECT 'order_items',    count(*) FROM order_items
UNION ALL SELECT 'order_payments', count(*) FROM order_payments
UNION ALL SELECT 'order_reviews',  count(*) FROM order_reviews
ORDER BY 1;
