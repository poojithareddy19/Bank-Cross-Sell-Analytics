-- One row per snapshot month present in the data.

CREATE OR REPLACE TABLE dim_month AS
SELECT
    month_index,
    snapshot_date,
    strftime(snapshot_date, '%Y-%m') AS month_label,
    count(*) AS customers
FROM stg_holdings
GROUP BY month_index, snapshot_date;
