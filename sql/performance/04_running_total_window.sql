-- Pair 4, candidate: the same running count with SUM() OVER a cumulative window frame.
-- Each customer's rows are sorted by month once and summed in a single pass.
-- Output must equal 04_running_total_self_join.sql.

WITH running AS (
    SELECT
        sum(coalesce(is_active, 0)) OVER (
            PARTITION BY customer_id ORDER BY month_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS active_months_so_far
    FROM fact_monthly_holdings
)
SELECT active_months_so_far, count(*) AS customer_months
FROM running
GROUP BY active_months_so_far
ORDER BY active_months_so_far;
