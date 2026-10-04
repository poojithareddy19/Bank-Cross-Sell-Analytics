-- Engagement rate = active customers (is_active = 1) / customers present.
-- One trend row per month, then breakdowns for the latest snapshot month by segment,
-- tenure band (seniority_months) and number of products held. Each breakdown uses a
-- single month so every customer is counted once.

WITH latest AS (
    SELECT month_index, month_label FROM dim_month ORDER BY month_index DESC LIMIT 1
),
latest_rows AS (
    SELECT
        f.is_active,
        coalesce(f.segment, 'unknown') AS segment,
        CASE
            WHEN f.seniority_months IS NULL THEN 'unknown'
            WHEN f.seniority_months < 12 THEN '00-11 months'
            WHEN f.seniority_months < 36 THEN '12-35 months'
            WHEN f.seniority_months < 120 THEN '36-119 months'
            ELSE '120+ months'
        END AS tenure_band,
        CASE WHEN f.n_products >= 4 THEN '4+' ELSE f.n_products::VARCHAR END AS products_held
    FROM fact_monthly_holdings AS f
    JOIN latest USING (month_index)
),
grouped AS (
    SELECT 1 AS dimension_order, 'month' AS dimension, m.month_label AS group_value, m.month_label,
           count(*) AS customers, count(*) FILTER (WHERE f.is_active = 1) AS active
    FROM fact_monthly_holdings AS f
    JOIN dim_month AS m USING (month_index)
    GROUP BY m.month_label
    UNION ALL
    SELECT 2, 'segment', segment, (SELECT month_label FROM latest),
           count(*), count(*) FILTER (WHERE is_active = 1)
    FROM latest_rows GROUP BY segment
    UNION ALL
    SELECT 3, 'tenure_band', tenure_band, (SELECT month_label FROM latest),
           count(*), count(*) FILTER (WHERE is_active = 1)
    FROM latest_rows GROUP BY tenure_band
    UNION ALL
    SELECT 4, 'products_held', products_held, (SELECT month_label FROM latest),
           count(*), count(*) FILTER (WHERE is_active = 1)
    FROM latest_rows GROUP BY products_held
)
SELECT dimension, group_value, month_label, customers, active, active / customers AS engagement_rate
FROM grouped
ORDER BY dimension_order, group_value;
