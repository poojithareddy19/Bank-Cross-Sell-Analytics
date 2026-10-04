-- severity: warning
-- description: Customer is missing from at least one month between first and last appearance; events across the gap are not counted.

SELECT
    customer_id,
    first_seen_month_index,
    last_seen_month_index,
    months_present,
    last_seen_month_index - first_seen_month_index + 1 - months_present AS missing_months
FROM dim_customer
WHERE last_seen_month_index - first_seen_month_index + 1 > months_present
ORDER BY customer_id;
