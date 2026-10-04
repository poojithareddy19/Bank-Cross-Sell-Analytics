-- Pair 4, baseline: running count of active months per customer with a RANGE self-join.
-- Every customer-month row is joined to all of that customer's rows up to the same month,
-- so a customer seen n months produces n(n+1)/2 joined rows before they are summed.
-- Output must equal 04_running_total_window.sql.

WITH running AS (
    SELECT a.customer_id, a.month_index, sum(coalesce(b.is_active, 0)) AS active_months_so_far
    FROM fact_monthly_holdings AS a
    JOIN fact_monthly_holdings AS b
      ON b.customer_id = a.customer_id AND b.month_index <= a.month_index
    GROUP BY a.customer_id, a.month_index
)
SELECT active_months_so_far, count(*) AS customer_months
FROM running
GROUP BY active_months_so_far
ORDER BY active_months_so_far;
