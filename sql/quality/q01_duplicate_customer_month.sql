-- severity: critical
-- description: More than one row for the same customer and snapshot month.

SELECT customer_id, month_index, count(*) AS row_count
FROM stg_holdings
GROUP BY customer_id, month_index
HAVING count(*) > 1
ORDER BY customer_id, month_index;
