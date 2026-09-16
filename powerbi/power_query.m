// RetailLens - Power Query (M) transformation layer
//
// One query per block. In Power BI Desktop: Home > Transform data > New Source >
// Blank Query > Advanced Editor, then paste a block and name the query to match
// the header.
//
// Two source modes:
//   CSV        SourcePath points at data/raw - what the repo ships with.
//   PostgreSQL swap RawOrders etc. for PostgreSQL.Database("host", "retaillens")
//              and the marts in sql/03_marts.sql load directly as views.
//
// Why it matters which one: against PostgreSQL, Table.Group and Table.SelectRows
// fold into GROUP BY and WHERE and the server does the work. Against CSV nothing
// folds and the mashup engine loads all 112,650 item rows into memory first. The
// M below is written to stay foldable - no Table.Buffer, no custom functions
// inside the group step - so pointing it at Postgres is a one-line change.


// === Parameter: SourcePath ================================================
// Manage Parameters > New. Type Text. Set to the absolute path of data/raw.
"C:\Users\<you>\Documents\GitHub\RetailLens\data\raw" meta [
    IsParameterQuery = true,
    Type = "Text",
    IsParameterQueryRequired = true
]


// === Function: LoadCsv ====================================================
// Every raw file is loaded the same way, so the load is written once. Encoding
// 65001 is required: Brazilian category and city names carry accents, and the
// default ANSI codepage mangles them into replacement characters.
let
    LoadCsv = (fileName as text) as table =>
        let
            Source = Csv.Document(
                File.Contents(SourcePath & "\" & fileName),
                [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
            ),
            Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
        in
            Promoted
in
    LoadCsv


// === Query: RawOrders =====================================================
let
    Source = LoadCsv("olist_orders_dataset.csv"),
    // Typed explicitly rather than by detection. The delivery columns are
    // genuinely null for orders that never arrived, and letting Power Query
    // guess risks an Any-typed column that silently breaks date arithmetic.
    Typed = Table.TransformColumnTypes(
        Source,
        {
            {"order_id", type text},
            {"customer_id", type text},
            {"order_status", type text},
            {"order_purchase_timestamp", type datetime},
            {"order_approved_at", type datetime},
            {"order_delivered_carrier_date", type datetime},
            {"order_delivered_customer_date", type datetime},
            {"order_estimated_delivery_date", type datetime}
        }
    )
in
    Typed


// === Query: RawOrderItems =================================================
let
    Source = LoadCsv("olist_order_items_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {
            {"order_id", type text},
            {"order_item_id", Int64.Type},
            {"product_id", type text},
            {"seller_id", type text},
            {"shipping_limit_date", type datetime},
            {"price", Currency.Type},
            {"freight_value", Currency.Type}
        }
    )
in
    Typed


// === Query: RawOrderPayments ==============================================
let
    Source = LoadCsv("olist_order_payments_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {
            {"order_id", type text},
            {"payment_sequential", Int64.Type},
            {"payment_type", type text},
            {"payment_installments", Int64.Type},
            {"payment_value", Currency.Type}
        }
    )
in
    Typed


// === Query: RawOrderReviews ===============================================
let
    Source = LoadCsv("olist_order_reviews_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {
            {"review_id", type text},
            {"order_id", type text},
            {"review_score", Int64.Type},
            {"review_creation_date", type datetime},
            {"review_answer_timestamp", type datetime}
        }
    ),
    // Comment text is not used by any visual and is the bulk of the file.
    Trimmed = Table.SelectColumns(
        Typed,
        {"review_id", "order_id", "review_score", "review_creation_date",
         "review_answer_timestamp"}
    )
in
    Trimmed


// === Query: ReviewsDeduped ================================================
// The published reviews file has duplicate review_ids and a few orders with more
// than one review. Sort newest-answered first, then Table.Distinct on order_id
// keeps the first row per key - one review per order.
let
    Source = RawOrderReviews,
    Sorted = Table.Sort(Source, {{"review_answer_timestamp", Order.Descending}}),
    Deduped = Table.Distinct(Sorted, {"order_id"}),
    Kept = Table.SelectColumns(Deduped, {"order_id", "review_score", "review_creation_date"})
in
    Kept


// === Query: ItemsByOrder ==================================================
// Collapse item lines to order grain BEFORE anything joins to RawOrders.
// Skipping this step is how an order with 3 items gets counted 3 times.
let
    Source = RawOrderItems,
    Grouped = Table.Group(
        Source,
        {"order_id"},
        {
            {"item_count", each Table.RowCount(_), Int64.Type},
            {"seller_count", each List.Count(List.Distinct(_[seller_id])), Int64.Type},
            {"product_revenue", each List.Sum(_[price]), Currency.Type},
            {"freight_value", each List.Sum(_[freight_value]), Currency.Type}
        }
    ),
    WithOrderValue = Table.AddColumn(
        Grouped,
        "order_value",
        each [product_revenue] + [freight_value],
        Currency.Type
    )
in
    WithOrderValue


// === Query: PaymentsByOrder ===============================================
// Same idea for payment splits. primary_payment_type is the split carrying the
// most money, which is the one worth showing on a slicer.
let
    Source = RawOrderPayments,
    Grouped = Table.Group(
        Source,
        {"order_id"},
        {
            {"payment_value", each List.Sum(_[payment_value]), Currency.Type},
            {"payment_splits", each Table.RowCount(_), Int64.Type},
            {"max_installments", each List.Max(_[payment_installments]), Int64.Type},
            {"primary_payment_type",
             each Table.Sort(_, {{"payment_value", Order.Descending}})[payment_type]{0},
             type text}
        }
    )
in
    Grouped


// === Query: Fct_Order =====================================================
// Grain: one row per order. All three joins below are 1:1 by construction,
// because each right-hand table was already collapsed to order grain.
let
    Source = RawOrders,

    JoinCustomers = Table.ExpandTableColumn(
        Table.NestedJoin(Source, {"customer_id"}, RawCustomers, {"customer_id"},
                         "cust", JoinKind.LeftOuter),
        "cust", {"customer_unique_id", "customer_state", "customer_city"}
    ),
    JoinItems = Table.ExpandTableColumn(
        Table.NestedJoin(JoinCustomers, {"order_id"}, ItemsByOrder, {"order_id"},
                         "items", JoinKind.LeftOuter),
        "items", {"item_count", "seller_count", "product_revenue", "freight_value",
                  "order_value"}
    ),
    JoinPayments = Table.ExpandTableColumn(
        Table.NestedJoin(JoinItems, {"order_id"}, PaymentsByOrder, {"order_id"},
                         "pay", JoinKind.LeftOuter),
        "pay", {"payment_value", "payment_splits", "max_installments",
                "primary_payment_type"}
    ),
    JoinReviews = Table.ExpandTableColumn(
        Table.NestedJoin(JoinPayments, {"order_id"}, ReviewsDeduped, {"order_id"},
                         "rev", JoinKind.LeftOuter),
        "rev", {"review_score"}
    ),

    AddIsCancelled = Table.AddColumn(
        JoinReviews, "is_cancelled",
        each List.Contains({"canceled", "unavailable"}, [order_status]), type logical
    ),
    AddIsDelivered = Table.AddColumn(
        AddIsCancelled, "is_delivered",
        each [order_delivered_customer_date] <> null, type logical
    ),
    AddDeliveryDays = Table.AddColumn(
        AddIsDelivered, "delivery_days",
        each if [order_delivered_customer_date] = null then null
             else Duration.TotalDays(
                 [order_delivered_customer_date] - [order_purchase_timestamp]),
        type number
    ),
    AddEstimatedDays = Table.AddColumn(
        AddDeliveryDays, "estimated_days",
        each Duration.TotalDays(
            [order_estimated_delivery_date] - [order_purchase_timestamp]),
        type number
    ),
    // Compared as dates, not datetimes: the promise is a day, so arriving at
    // 23:00 on the promised day is on time, not 0.96 days late.
    AddDelayDays = Table.AddColumn(
        AddEstimatedDays, "delay_days",
        each if [order_delivered_customer_date] = null then null
             else Duration.Days(
                 Date.From([order_delivered_customer_date])
                 - Date.From([order_estimated_delivery_date])),
        Int64.Type
    ),
    // null, not false, when the order never arrived. An undelivered order is
    // unknown, and averaging it in as "on time" would understate the late rate.
    AddIsLate = Table.AddColumn(
        AddDelayDays, "is_late",
        each if [delay_days] = null then null else [delay_days] > 0, type logical
    ),
    AddBucket = Table.AddColumn(
        AddIsLate, "delivery_bucket",
        each if [delay_days] = null then null
             else if [delay_days] <= -10 then "10+ days early"
             else if [delay_days] <= -3 then "3-10 days early"
             else if [delay_days] <= 0 then "0-3 days early"
             else if [delay_days] <= 3 then "1-3 days late"
             else if [delay_days] <= 10 then "3-10 days late"
             else "10+ days late",
        type text
    ),
    AddOrderDate = Table.AddColumn(
        AddBucket, "order_date", each Date.From([order_purchase_timestamp]), type date
    )
in
    AddOrderDate


// === Query: Fct_OrderItem =================================================
// Grain: one row per item line. The only correct grain for category and seller
// analysis. Never sum payment_value against this table.
let
    Source = RawOrderItems,
    JoinOrder = Table.ExpandTableColumn(
        Table.NestedJoin(Source, {"order_id"}, Fct_Order, {"order_id"},
                         "ord", JoinKind.Inner),
        "ord", {"customer_state", "order_status", "is_cancelled", "is_delivered",
                "order_date", "delivery_days", "delay_days", "is_late",
                "delivery_bucket", "review_score"}
    ),
    JoinProduct = Table.ExpandTableColumn(
        Table.NestedJoin(JoinOrder, {"product_id"}, Dim_Product, {"product_id"},
                         "prod", JoinKind.LeftOuter),
        "prod", {"product_category"}
    ),
    FillCategory = Table.ReplaceValue(
        JoinProduct, null, "Unknown", Replacer.ReplaceValue, {"product_category"}
    ),
    AddItemValue = Table.AddColumn(
        FillCategory, "item_value", each [price] + [freight_value], Currency.Type
    )
in
    AddItemValue


// === Query: Dim_Product ===================================================
let
    Source = LoadCsv("olist_products_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"product_id", type text}, {"product_category_name", type text},
         {"product_weight_g", type number}}
    ),
    JoinTranslation = Table.ExpandTableColumn(
        Table.NestedJoin(Typed, {"product_category_name"}, RawCategoryTranslation,
                         {"product_category_name"}, "tr", JoinKind.LeftOuter),
        "tr", {"product_category_name_english"}
    ),
    // 623 products carry no category, and a few categories are absent from the
    // translation file. Both fall back explicitly instead of vanishing from the
    // category page as blanks.
    AddCategory = Table.AddColumn(
        JoinTranslation, "product_category",
        each Text.Proper(
            Text.Replace(
                [product_category_name_english] ?? [product_category_name] ?? "unknown",
                "_", " ")),
        type text
    ),
    Kept = Table.SelectColumns(AddCategory,
        {"product_id", "product_category", "product_weight_g"})
in
    Kept


// === Query: RawCategoryTranslation ========================================
let
    Source = LoadCsv("product_category_name_translation.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"product_category_name", type text},
         {"product_category_name_english", type text}}
    )
in
    Typed


// === Query: RawCustomers / Dim_Customer ===================================
let
    Source = LoadCsv("olist_customers_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"customer_id", type text}, {"customer_unique_id", type text},
         {"customer_zip_code_prefix", type text}, {"customer_city", type text},
         {"customer_state", type text}}
    )
in
    Typed


// === Query: Dim_Seller ====================================================
let
    Source = LoadCsv("olist_sellers_dataset.csv"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"seller_id", type text}, {"seller_zip_code_prefix", type text},
         {"seller_city", type text}, {"seller_state", type text}}
    )
in
    Typed


// === Query: Dim_Date ======================================================
// Generated from the data range rather than hardcoded, so it cannot silently
// stop covering the facts when the source is refreshed. Mark as a date table on
// [Date] so DATEADD and DATESYTD in measures.dax work.
let
    MinDate = Date.From(List.Min(Fct_Order[order_purchase_timestamp])),
    MaxDate = Date.From(List.Max(Fct_Order[order_purchase_timestamp])),
    // Pad to whole years so year-over-year visuals have complete axes.
    StartDate = #date(Date.Year(MinDate), 1, 1),
    EndDate = #date(Date.Year(MaxDate), 12, 31),
    DayCount = Duration.Days(EndDate - StartDate) + 1,
    Dates = List.Dates(StartDate, DayCount, #duration(1, 0, 0, 0)),
    AsTable = Table.FromList(Dates, Splitter.SplitByNothing(), {"Date"}),
    Typed = Table.TransformColumnTypes(AsTable, {{"Date", type date}}),
    AddYear = Table.AddColumn(Typed, "Year", each Date.Year([Date]), Int64.Type),
    AddMonthNo = Table.AddColumn(AddYear, "MonthNo", each Date.Month([Date]), Int64.Type),
    AddMonth = Table.AddColumn(AddMonthNo, "Month",
        each Date.ToText([Date], [Format = "MMM", Culture = "en-US"]), type text),
    // Sortable label: without it, a month axis sorts alphabetically and
    // "Apr 2018" lands before "Jan 2018".
    AddYearMonth = Table.AddColumn(AddMonth, "Year Month",
        each Date.ToText([Date], [Format = "yyyy-MM"]), type text),
    AddQuarter = Table.AddColumn(AddYearMonth, "Quarter",
        each "Q" & Text.From(Date.QuarterOfYear([Date])), type text),
    AddMonthStart = Table.AddColumn(AddQuarter, "Month Start",
        each Date.StartOfMonth([Date]), type date)
in
    AddMonthStart
