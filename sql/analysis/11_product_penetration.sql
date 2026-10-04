-- Penetration = holders / customers present, per snapshot month and product.

WITH monthly AS (
    SELECT month_index, count(*) AS customers, sum(COLUMNS('^ind_.*_ult1$'))
    FROM fact_monthly_holdings
    GROUP BY month_index
),
long AS (
    UNPIVOT monthly ON COLUMNS('^ind_.*_ult1$') INTO NAME product_code VALUE holders
)
SELECT
    m.month_label,
    p.product_code,
    p.product_name,
    p.family,
    l.holders::BIGINT AS holders,
    l.customers,
    l.holders / l.customers AS penetration
FROM long AS l
JOIN dim_month AS m USING (month_index)
JOIN dim_product AS p USING (product_code)
ORDER BY m.month_index, p.catalogue_order;
