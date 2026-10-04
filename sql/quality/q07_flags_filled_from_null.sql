-- severity: warning
-- description: Customer-month rows where at least one NULL product flag was filled with 0.

SELECT customer_id, month_index, flags_filled_from_null
FROM stg_holdings
WHERE flags_filled_from_null > 0
ORDER BY customer_id, month_index;
