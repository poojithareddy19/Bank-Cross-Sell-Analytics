-- Customer x month fact table. It stays wide (24 TINYINT flag columns): a long
-- customer x month x product table would have 24 times the rows. Events are
-- derived from this table with one LAG window pass (sql/analysis/10_product_events.sql).

CREATE OR REPLACE TABLE fact_monthly_holdings AS
SELECT
    customer_id,
    month_index,
    COLUMNS('^ind_.*_ult1$'),
    list_sum([*COLUMNS('^ind_.*_ult1$')])::TINYINT AS n_products,
    is_active,
    seniority_months,
    segment,
    relation_type,
    is_new_customer
FROM stg_holdings;
