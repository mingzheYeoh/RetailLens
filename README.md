# RetailLens

**E-commerce sales and delivery analysis on the public Olist dataset.**
Python · pandas · SQL / PostgreSQL · Power BI · Power Query (M) · DAX

99,441 orders across four tables at three different grains, joined without
double-counting, modelled into a star schema, and reconciled twice — once in
pandas, once in SQL — before a single number reaches a dashboard.

---

## The finding

**Customers punish the broken promise, not the wait.**

Olist delivers in **12.6 days** against a promise of **23.7**. Only **6.8%** of
orders miss the promised date — but those orders produce **36.5% of all one-star
reviews** and average **2.26 stars against 4.28** for orders that arrive on time.

Across Brazil's 27 states, *average delivery time* explains satisfaction weakly
(**r = −0.44**). The *share of orders delivered late* explains it strongly
(**r = −0.89**). Amazonas takes 26 days and rates 4.23; Alagoas takes 24 days and
rates 3.83 — because Amazonas promises 45 days and Alagoas promises 33.

![Delivery time and late rate against review score, by state](reports/figures/04_state_delivery.png)

Full write-up, with limitations and next steps: **[reports/business_report.md](reports/business_report.md)**

---

## The part that is actually hard

The four source tables sit at three grains, and they fan out against each other:

| Table | Grain | Rows |
|---|---|---:|
| `orders` | one row per order | 99,441 |
| `order_items` | one row per **item line** | 112,650 |
| `order_payments` | one row per **payment split** | 103,886 |
| `order_reviews` | ~one per order, with duplicates | 100,000 |

Join them directly and you get **117,601 rows for 99,441 orders**. Summing over
that result overstates item revenue by **4.6%** and payment value by **26.9%**.

The asymmetry is what makes it dangerous: 4.6% looks completely plausible on a
dashboard, so the error ships. `sql/04_reconciliation.sql` computes both numbers
so the failure mode is visible rather than theoretical.

**The fix:** items and payments are each collapsed to order grain *before* they
join to `orders`, so every join into the fact table is 1:1. Order-level money is
measured on `Fct_Order`; product-level money on `Fct_OrderItem`; the two are never
summed together.

```
             collapse first              then join 1:1
order_items ────GROUP BY order_id────┐
order_payments ─GROUP BY order_id────┼──▶ Fct_Order  (99,441 rows, order grain)
order_reviews ──dedupe to 1/order────┘
                                     └──▶ Fct_OrderItem (112,650 rows, item grain)
```

---

## Reconciliation

`python src/run_sql_checks.py` runs `sql/03_marts.sql` and
`sql/04_reconciliation.sql` and then recomputes four KPI views in SQL and compares
them, row by row, against the pandas pipeline:

```
                              check_name    status    expected      actual
          fct_order is one row per order      PASS       99441       99441
fct_order_item preserves every item line      PASS      112650      112650
 product revenue ties to raw item prices      PASS  13591643.7  13591643.7
          order value ties to item grain      PASS 15843553.24 15843553.24
              payments equal order value KNOWN GAP 15843553.24 16008872.12
           every order has a payment row KNOWN GAP           0           1

Naive orders-items-payments join: 117,601 rows for 99,441 orders
  revenue  16,566,543.85 vs 15,843,553.24 correct  (+4.6%)
  payments overstated by +26.9%

SQL vs pandas, same KPI computed twice:
  match  monthly sales           24 rows, 4 measures
  match  category performance    74 rows, 6 measures
  match  state performance       27 rows, 5 measures
  match  delivery vs review       6 rows, 3 measures
```

The two `KNOWN GAP` rows are findings, not bugs. Payments run 1.04% above item
value because installment interest and voucher top-ups are charged to the customer
but are not revenue for goods sold — so every sales KPI uses item value. One order
has no payment record upstream and is left `NULL` rather than imputed to zero.
Widening a tolerance until those rows turn green would hide both.

---

## Findings

### Sales are growing; order size is not

![Monthly revenue and average order value](reports/figures/01_monthly_sales.png)

Revenue grew to a R$1.17M peak in Nov 2017 and settled around R$1.0–1.15M. AOV is
**flat at R$160** for the whole period at **1.14 items per order** — growth comes
entirely from order count. Only **3.0%** of customers ever order twice.

### A late delivery costs about two stars, with a cliff at day three

![Average review score by delivery bucket](reports/figures/02_delivery_vs_review.png)

Slipping 1–3 days costs ~0.8 stars. Slipping past 3 days costs another 1.3. Past
10 days, extra delay barely registers — the customer has already given the lowest
score there is. A recovery action worth taking on day 2 is worth far less on day 12.

### Category ratings are not a delivery story

![Category revenue and average review](reports/figures/03_category_performance.png)

Across the 30 categories with 500+ item lines: late rate correlates −0.51 with
review score, freight share −0.23, and **price −0.02**. Office Furniture is the
worst-rated category at 3.48 — but its *on-time* orders rate 3.61 against a 4.20
benchmark, while its *late* orders rate the same as everyone else's. Whatever is
wrong there is not logistics.

---

## Power BI

The `.pbix` is built from the artefacts in `powerbi/`, which are the parts worth
reviewing in a repository:

| File | What it is |
|---|---|
| [`powerbi/KPI_DEFINITIONS.md`](powerbi/KPI_DEFINITIONS.md) | Every KPI defined once — population filters, grain, gotchas, and 7 documented limitations |
| [`powerbi/measures.dax`](powerbi/measures.dax) | 31 DAX measures, each matching a SQL definition, including two `Grain Check` guard rails for a hidden diagnostics page |
| [`powerbi/power_query.m`](powerbi/power_query.m) | The M transformation layer — the same collapse-then-join logic, written to stay query-foldable so pointing it at PostgreSQL is a one-line change |

Four report pages — sales overview, order value, delivery performance, product
categories — with synced date, state and category slicers. Page specs are in
`KPI_DEFINITIONS.md`.

The charts in this README are matplotlib exports of the same measures, rendered by
`src/make_figures.py`, so the repo shows its numbers without needing Power BI
Desktop installed.

---

## Run it

```bash
pip install -r requirements.txt

python scripts/download_data.py    # 65 MB of CSVs into data/raw/ (gitignored)
python src/build_marts.py          # star schema + reconciliation + report tables
python src/run_sql_checks.py       # run the SQL, compare it against pandas
python src/make_figures.py         # the four figures
python tests/test_marts.py         # 8 grain / delivery-logic checks, no data needed
```

Against a real PostgreSQL server:

```bash
createdb retaillens
psql -d retaillens -v ON_ERROR_STOP=1 -f sql/01_schema.sql
psql -d retaillens -v ON_ERROR_STOP=1 -f sql/02_load.sql
psql -d retaillens -v ON_ERROR_STOP=1 -f sql/03_marts.sql
psql -d retaillens -v ON_ERROR_STOP=1 -f sql/04_reconciliation.sql
```

`run_sql_checks.py` executes those same `.sql` files through DuckDB so the checks
run locally without a server. The only engine difference is `initcap()`, which
Postgres has and DuckDB does not; the runner shims it in one macro rather than
putting an engine branch in the SQL.

---

## Repository

```
scripts/download_data.py      fetch the 8 Olist CSVs
src/build_marts.py            pandas ETL - grain discipline lives here
src/run_sql_checks.py         run the SQL, tie it out against pandas
src/make_figures.py           four figures, validated 2-colour palette
sql/01_schema.sql             PostgreSQL DDL, typed and constrained
sql/02_load.sql               \copy loads
sql/03_marts.sql              star schema as views
sql/04_reconciliation.sql     the checks + the fan-out demonstration
powerbi/                      DAX, Power Query M, KPI dictionary
reports/business_report.md    the write-up
reports/tables/*.csv          small aggregates the report cites
tests/test_marts.py           grain + delivery logic, on a 4-order fixture
```

---

## Notes on the data

Source: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
(Kaggle, CC BY-NC-SA 4.0), 2016-09 to 2018-09. `download_data.py` pulls
byte-identical copies from public GitHub mirrors so the pipeline runs without
Kaggle credentials.

The reviews file differs between published mirrors — the copy used here has
100,000 rows and 99,173 distinct review ids covering every order, where commonly
cited analyses use a ~99.2k-row file with incomplete coverage. Review percentages
here are therefore not directly comparable to those write-ups. This and six other
limitations are documented in
[`powerbi/KPI_DEFINITIONS.md`](powerbi/KPI_DEFINITIONS.md#known-limitations).

Findings are associations from observational data, not causal estimates. The
report says where that matters and what would be needed to do better.
