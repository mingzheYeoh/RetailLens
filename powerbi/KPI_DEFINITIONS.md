# KPI definitions

Every number on a RetailLens report page is defined here once, implemented twice
(DAX in `measures.dax`, SQL in `../sql/03_marts.sql`), and tied out by
`../sql/04_reconciliation.sql`. If a figure on a dashboard cannot be re-derived
from this page, treat the figure as wrong.

Currency is Brazilian real (R$). Dataset covers **2016-09-04 to 2018-09-03**.

---

## The rule that governs every measure

The source has three different grains, and they fan out against each other:

| Table | Grain | Rows | Fan-out vs orders |
|---|---|---:|---|
| `orders` | one row per order | 99,441 | 1.00x |
| `order_items` | one row per **item line** | 112,650 | 1.13x |
| `order_payments` | one row per **payment split** | 103,886 | 1.04x |
| `order_reviews` | ~one row per order, with duplicates | 100,000 | 1.01x |

Joining `orders → order_items → order_payments` directly produces **117,601 rows
for 99,441 orders**. Summing over that result overstates item revenue by **4.6%**
and payment value by **26.9%**. The asymmetry is the dangerous part: 4.6% looks
plausible on a dashboard, so the error passes review.

So: **items and payments are each collapsed to order grain before they join to
`orders`.** Order-level money is measured on `Fct_Order`; product-level money is
measured on `Fct_OrderItem`; the two are never added together.

---

## Population filters

Applied consistently, and named so a measure never re-states a filter inline.

| Name | Rule | Rows | Why |
|---|---|---:|---|
| **Sales population** | `order_status NOT IN ('canceled','unavailable')` | 98,207 orders | These orders never became revenue. |
| **Delivered population** | Sales population **and** `order_delivered_customer_date IS NOT NULL` | 96,470 orders | 1,737 orders (1.77%) never arrived. They are not "on time" — they are unknown. |
| **Reviewed population** | Sales population **and** `review_score IS NOT NULL` | 98,207 orders | See the coverage caveat at the bottom. |

---

## Sales KPIs

| KPI | Definition | Grain | Value |
|---|---|---|---:|
| **Total Revenue** | `SUM(price + freight_value)` over the sales population | Order | R$15,735,527 |
| **Product Revenue** | `SUM(price)`, freight excluded | Order | R$13,494,401 |
| **Total Orders** | `DISTINCTCOUNT(order_id)` over the sales population | Order | 98,207 |
| **Total Customers** | `DISTINCTCOUNT(customer_unique_id)` | Order | 94,990 |
| **Average Order Value** | `Total Revenue / Total Orders` | Order | R$160.24 |
| **Items per Order** | `SUM(item_count) / Total Orders` | Order | 1.14 |
| **Revenue MoM %** | Revenue vs the prior calendar month via `DATEADD` | Order | — |

**Revenue uses item value, not payment value.** Payments total R$16,008,872
against an item value of R$15,843,553 — 1.04% higher, because installment
interest and voucher top-ups are charged to the customer but are not revenue for
goods sold. Reporting payments as revenue would overstate sales by ~R$165k.

**`customer_unique_id`, never `customer_id`.** Olist issues a fresh `customer_id`
for every order, so counting `customer_id` just recounts orders. The distinction
matters: repeat-purchase rate is **3.0%**, which is only visible with the right
key.

---

## Delivery KPIs

Measured over the delivered population only.

| KPI | Definition | Value |
|---|---|---:|
| **Avg Delivery Days** | `delivered_customer_date − purchase_timestamp`, in days | 12.56 (median 10.2) |
| **Avg Promised Days** | `estimated_delivery_date − purchase_timestamp` | 23.74 |
| **Avg Promise Padding** | Promised − actual | 11.18 days |
| **Late Orders** | `delivered_customer_date::date > estimated_delivery_date::date` | 6,534 |
| **Late Delivery Rate** | `Late Orders / Delivered Orders` | 6.77% |
| **Revenue at Risk** | Revenue of late-delivered orders | R$1,150,892 (7.3%) |

**Late is compared date to date, not timestamp to timestamp.** Olist promises a
*day*, not an instant, so an order arriving at 23:00 on the promised date is on
time. Comparing raw timestamps would mark it 0.96 days late and inflate the late
rate.

**The denominator is delivered orders, not all orders.** Quoting late rate over
all orders would dilute it with the 1.77% that have no delivery date at all.

**Delivery buckets** used on the delivery page, from `delay_days`:
`10+ days early` ≤ −10 · `3-10 days early` ≤ −3 · `0-3 days early` ≤ 0 ·
`1-3 days late` ≤ 3 · `3-10 days late` ≤ 10 · `10+ days late` > 10.

---

## Review KPIs

| KPI | Definition | Value |
|---|---|---:|
| **Avg Review Score** | `AVERAGE(review_score)`, 1–5 | 4.10 |
| **One Star Rate** | share of reviews scoring 1 | 11.1% |
| **Five Star Rate** | share of reviews scoring 5 | 58.0% |
| **Review Gap Late vs On Time** | Avg score late − Avg score on time | **−2.02** |

Orders with no review are excluded by `AVERAGE`, not counted as zero — a zero
would sit below the 1–5 scale and drag the mean down.

**One review per order.** The published reviews file has 100,000 rows but only
99,173 distinct `review_id`s. Duplicates are resolved by keeping the most
recently answered review per order: that is the score reflecting the closed
interaction. Averaging duplicates instead shifts scores slightly — see
limitations.

---

## Category KPIs (item grain)

A category belongs to a product, not to an order, so these are measured on
`Fct_OrderItem`. A four-item order spanning three categories contributes to all
three. Summing **Category Revenue** across all categories returns **Total
Revenue** exactly — the check that proves the grain is right.

| KPI | Definition |
|---|---|
| **Category Revenue** | `SUM(price + freight_value)` over item lines |
| **Items Sold** | `COUNTROWS(Fct_OrderItem)` |
| **Avg Item Price** | `AVERAGE(price)` |
| **Freight Share of Item Value** | `SUM(freight_value) / Category Revenue` |
| **Category Late Rate** | late item lines / delivered item lines |
| **Category Avg Review** | `AVERAGE(review_score)` at item grain |

Category late rate is *order*-driven — every line in an order shares the order's
delivery outcome — so it reads as "the late rate experienced by buyers of this
category", not "how often this product ships late".

---

## Report pages

| Page | Visuals | Answers |
|---|---|---|
| **1. Sales overview** | KPI cards (Revenue, Orders, AOV, Customers), revenue by month with MoM %, revenue by state map | How big is the business and which way is it moving? |
| **2. Order value** | AOV trend, order value distribution, items-per-order, payment type and installment mix | What does a typical order look like and what drives its size? |
| **3. Delivery performance** | Late rate trend, delivery-days distribution vs promise, delay buckets, state table with late rate and avg review | Are we keeping the delivery promise, and where are we not? |
| **4. Product categories** | Revenue treemap, top-20 category table with avg review, freight share and late rate, price vs review scatter | Which categories carry the revenue and which carry the complaints? |

**Slicers on every page**, synced: `Dim_Date[Date]` (range), `customer_state`
(multi-select), `product_category` (multi-select, category page only).

**Hidden diagnostics page** carries the `Grain Check` and `Fact Tables Tie Out`
measures. If either stops reading `OK`, a relationship has gone many-to-many or a
filter direction has been flipped, and every total on the report is suspect.

---

## Known limitations

1. **Payments exceed item value by 1.04%.** Installment interest and vouchers.
   Sales KPIs use item value; payment value is reported separately on page 2 and
   never as revenue.
2. **One order has no payment record.** Left `NULL`, not imputed to zero, so
   payment-side measures exclude it rather than understate it.
3. **1,737 orders (1.77%) never reached the customer.** They are excluded from
   delivery and review averages, not counted as on-time.
4. **The reviews file differs between published mirrors.** The copy used here has
   100,000 rows covering all 99,441 orders, giving 100% review coverage. Widely
   cited Olist analyses work from a ~99.2k-row file with incomplete coverage, so
   review percentages here are not directly comparable to those write-ups.
5. **September 2018 is a stub** — the data cuts off at 2018-09-03 with a single
   order in that month. Every monthly trend excludes the final partial month, or
   the chart shows a cliff that is a collection artefact, not a business event.
6. **Promise padding is a business decision, not a measurement.** An average
   11.18-day buffer means "on time" is a soft target. Late rate against a
   tightened SLA would be materially higher; that alternative is quantified in
   the report rather than assumed here.
7. **623 products have no category.** Bucketed as `Unknown` (1,589 item lines,
   R$207k) rather than dropped, so category revenue still sums to total revenue.
