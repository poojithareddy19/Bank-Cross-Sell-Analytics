-- severity: critical
-- description: Star schema row counts do not reconcile with the staging table.

WITH counts AS (
    SELECT 'fact_monthly_holdings' AS table_name,
           (SELECT count(*) FROM fact_monthly_holdings) AS actual_rows,
           (SELECT count(*) FROM stg_holdings) AS expected_rows
    UNION ALL
    SELECT 'dim_customer',
           (SELECT count(*) FROM dim_customer),
           (SELECT count(DISTINCT customer_id) FROM stg_holdings)
    UNION ALL
    SELECT 'dim_month',
           (SELECT count(*) FROM dim_month),
           (SELECT count(DISTINCT month_index) FROM stg_holdings)
)
SELECT * FROM counts WHERE actual_rows <> expected_rows;
