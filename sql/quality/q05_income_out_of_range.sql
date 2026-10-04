-- severity: warning
-- description: Income is negative or above quality.income_max_eur.

SELECT customer_id, month_index, income
FROM stg_holdings
WHERE income < 0 OR income > $income_max
ORDER BY income DESC, customer_id, month_index;
