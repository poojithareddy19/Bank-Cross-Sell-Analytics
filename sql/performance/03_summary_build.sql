-- Pair 3, one-off build of the materialised per-customer aggregate table. Its cost is
-- timed separately so the report can state after how many queries it pays for itself.

CREATE OR REPLACE TABLE perf_customer_summary AS
SELECT
    customer_id,
    count(*) AS months_present,
    avg(n_products) AS avg_products,
    max(n_products) AS max_products,
    max(is_active) AS ever_active
FROM fact_monthly_holdings
GROUP BY customer_id;
