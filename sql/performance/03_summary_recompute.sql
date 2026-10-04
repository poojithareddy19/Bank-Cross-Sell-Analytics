-- Pair 3, baseline: every query recomputes per-customer aggregates from the 24-flag fact
-- table (one GROUP BY over all customer-month rows) before answering its question.
-- Output must equal 03_summary_materialised.sql.

WITH customer_summary AS (
    SELECT
        customer_id,
        count(*) AS months_present,
        avg(n_products) AS avg_products,
        max(n_products) AS max_products,
        max(is_active) AS ever_active
    FROM fact_monthly_holdings
    GROUP BY customer_id
)
SELECT
    max_products,
    ever_active,
    count(*) AS customers,
    round(avg(avg_products), 6) AS mean_avg_products,
    round(avg(months_present), 6) AS mean_months_present
FROM customer_summary
GROUP BY max_products, ever_active
ORDER BY max_products, ever_active;
