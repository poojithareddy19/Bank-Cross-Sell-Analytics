-- severity: warning
-- description: Customer has no join date (fecha_alta) in any month; excluded from join cohorts.

SELECT customer_id, first_seen_month_index, months_present
FROM dim_customer
WHERE join_date IS NULL
ORDER BY customer_id;
