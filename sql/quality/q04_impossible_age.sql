-- severity: warning
-- description: Age outside the configured plausible range (quality.age_min to quality.age_max).

SELECT customer_id, month_index, age
FROM stg_holdings
WHERE age < $age_min OR age > $age_max
ORDER BY customer_id, month_index;
