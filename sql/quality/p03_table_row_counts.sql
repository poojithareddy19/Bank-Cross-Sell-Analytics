-- severity: profile
-- description: Row counts of every warehouse table.

SELECT 'stg_holdings' AS table_name, count(*) AS row_count FROM stg_holdings
UNION ALL SELECT 'fact_monthly_holdings', count(*) FROM fact_monthly_holdings
UNION ALL SELECT 'dim_customer', count(*) FROM dim_customer
UNION ALL SELECT 'dim_product', count(*) FROM dim_product
UNION ALL SELECT 'dim_month', count(*) FROM dim_month;
