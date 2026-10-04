-- Pair 3, candidate: the same question answered from the materialised table
-- (one row per customer instead of one row per customer-month).
-- Output must equal 03_summary_recompute.sql.

SELECT
    max_products,
    ever_active,
    count(*) AS customers,
    round(avg(avg_products), 6) AS mean_avg_products,
    round(avg(months_present), 6) AS mean_months_present
FROM perf_customer_summary
GROUP BY max_products, ever_active
ORDER BY max_products, ever_active;
