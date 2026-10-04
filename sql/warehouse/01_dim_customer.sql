-- One row per customer. "Latest" attributes come from the most recent month where
-- the attribute is not NULL; age is taken from the first month it is reported.

CREATE OR REPLACE TABLE dim_customer AS
SELECT
    customer_id,
    min(join_date) AS join_date,
    date_trunc('month', min(join_date))::DATE AS join_month,
    min(month_index) AS first_seen_month_index,
    max(month_index) AS last_seen_month_index,
    count(*) AS months_present,
    arg_max(segment, month_index) FILTER (WHERE segment IS NOT NULL) AS latest_segment,
    arg_max(sex, month_index) FILTER (WHERE sex IS NOT NULL) AS sex,
    arg_min(age, month_index) FILTER (WHERE age IS NOT NULL) AS age_at_first_seen,
    arg_max(province_name, month_index) FILTER (WHERE province_name IS NOT NULL) AS province,
    arg_max(channel, month_index) FILTER (WHERE channel IS NOT NULL) AS channel,
    arg_max(income, month_index) FILTER (WHERE income IS NOT NULL) AS income
FROM stg_holdings
GROUP BY customer_id;
