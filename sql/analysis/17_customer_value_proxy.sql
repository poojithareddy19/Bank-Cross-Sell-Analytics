-- Customer value PROXY for the latest snapshot month. The data has no revenue, so value
-- is approximated by breadth of relationship and capacity: products held, product
-- families held, income band and activity. Income bands are quartiles among customers
-- present in the latest month with a known income; the rest are 'missing'.

WITH latest AS (
    SELECT max(month_index) AS month_index FROM dim_month
),
holdings AS (
    SELECT f.*
    FROM fact_monthly_holdings AS f
    JOIN latest USING (month_index)
),
families AS (
    SELECT u.customer_id, count(DISTINCT p.family) AS families_held
    FROM (
        UNPIVOT (SELECT customer_id, COLUMNS('^ind_.*_ult1$') FROM holdings)
        ON COLUMNS('^ind_.*_ult1$') INTO NAME product_code VALUE flag
    ) AS u
    JOIN dim_product AS p USING (product_code)
    WHERE u.flag = 1
    GROUP BY u.customer_id
),
income_quartiles AS (
    SELECT h.customer_id, ntile(4) OVER (ORDER BY c.income) AS quartile
    FROM holdings AS h
    JOIN dim_customer AS c USING (customer_id)
    WHERE c.income IS NOT NULL
),
customers AS (
    SELECT
        h.n_products,
        coalesce(fm.families_held, 0) AS families_held,
        CASE WHEN h.is_active = 1 THEN 'active' WHEN h.is_active = 0 THEN 'inactive' ELSE 'unknown' END
            AS activity,
        CASE WHEN q.quartile IS NULL THEN 'missing' ELSE 'Q' || q.quartile END AS income_band
    FROM holdings AS h
    LEFT JOIN families AS fm USING (customer_id)
    LEFT JOIN income_quartiles AS q USING (customer_id)
)
SELECT
    income_band,
    activity,
    CASE WHEN families_held >= 3 THEN '3+' ELSE families_held::VARCHAR END AS families_held,
    count(*) AS customers,
    count(*) / sum(count(*)) OVER () AS share_of_customers,
    avg(n_products) AS avg_products
FROM customers
GROUP BY ALL
ORDER BY income_band, activity, families_held;
