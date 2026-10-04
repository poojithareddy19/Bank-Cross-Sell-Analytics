-- Propensity dataset: one row per sampled customer and feature month t, for customers
-- present in both t and t+1. Labels come from t+1; every feature uses months <= t only:
--   cur  = the customer's row at t (holdings, activity, segment, ...)
--   nxt  = the row at t+1, used ONLY for the two label columns
--   p1/p3 and the 3-month history joins look back to t-1, t-3 and t-2..t
-- adopts_target: target product not held at t and held at t+1 (rows holding it at t keep
-- holds_target_at_t = 1 and are excluded from that population in Python).
-- adopts_any: at least one adoption event in t+1 (fallback target, see config min_base_rate).
-- The target product's own flag is not a feature column.
-- Parameters: $target_product, $sample_threshold, $train_first, $train_last,
-- $validation_first, $validation_last, $test_first, $test_last (month indexes).

CREATE OR REPLACE TABLE propensity_dataset AS
WITH base AS (
    SELECT
        cur.customer_id,
        cur.month_index AS feature_month,
        nxt.month_index AS label_month,
        CASE
            WHEN cur.month_index BETWEEN $train_first AND $train_last THEN 'train'
            WHEN cur.month_index BETWEEN $validation_first AND $validation_last THEN 'validation'
            WHEN cur.month_index BETWEEN $test_first AND $test_last THEN 'test'
        END AS split,
        struct_extract(cur, $target_product) AS holds_target_at_t,
        struct_extract(nxt, $target_product) AS holds_target_next
    FROM fact_monthly_holdings AS cur
    JOIN fact_monthly_holdings AS nxt
      ON nxt.customer_id = cur.customer_id AND nxt.month_index = cur.month_index + 1
    WHERE cur.month_index BETWEEN $train_first AND $test_last
      AND (cur.customer_id * 2654435761) % 4294967296 < $sample_threshold
),
event_counts AS (
    SELECT
        customer_id,
        month_index,
        count(*) FILTER (WHERE event_type = 'adoption') AS adoptions,
        count(*) FILTER (WHERE event_type = 'attrition') AS attritions
    FROM product_events
    GROUP BY customer_id, month_index
),
history AS (
    -- t-2 .. t: activity and product events over the last three months
    SELECT
        b.customer_id,
        b.feature_month,
        sum(coalesce(h.is_active, 0)) AS active_months_last_3m,
        sum(coalesce(e.adoptions, 0)) AS adoptions_last_3m,
        sum(coalesce(e.attritions, 0)) AS attritions_last_3m
    FROM base AS b
    LEFT JOIN fact_monthly_holdings AS h
      ON h.customer_id = b.customer_id AND h.month_index BETWEEN b.feature_month - 2 AND b.feature_month
    LEFT JOIN event_counts AS e
      ON e.customer_id = h.customer_id AND e.month_index = h.month_index
    GROUP BY b.customer_id, b.feature_month
),
next_adoptions AS (
    SELECT DISTINCT customer_id, month_index
    FROM product_events
    WHERE event_type = 'adoption'
),
flags AS (
    SELECT customer_id, month_index, COLUMNS(c -> c LIKE 'ind_%' AND c <> $target_product)
    FROM fact_monthly_holdings
)
SELECT
    b.customer_id,
    b.feature_month,
    b.label_month,
    b.split,
    b.holds_target_at_t,
    CASE WHEN b.holds_target_at_t = 0 AND b.holds_target_next = 1 THEN 1 ELSE 0 END AS adopts_target,
    CASE WHEN a.customer_id IS NOT NULL THEN 1 ELSE 0 END AS adopts_any,
    flags.* EXCLUDE (customer_id, month_index),
    cur.n_products,
    cur.n_products - p1.n_products AS n_products_change_1m,
    cur.n_products - p3.n_products AS n_products_change_3m,
    b.feature_month - c.first_seen_month_index AS months_since_first_seen,
    hist.adoptions_last_3m,
    hist.attritions_last_3m,
    hist.active_months_last_3m,
    cur.is_active,
    cur.seniority_months,
    cur.is_new_customer,
    coalesce(cur.segment, 'unknown') AS segment,
    coalesce(cur.relation_type, 'unknown') AS relation_type,
    CASE
        WHEN s.age IS NULL THEN 'unknown'
        WHEN s.age < 25 THEN 'under 25'
        WHEN s.age < 35 THEN '25-34'
        WHEN s.age < 45 THEN '35-44'
        WHEN s.age < 55 THEN '45-54'
        WHEN s.age < 65 THEN '55-64'
        ELSE '65+'
    END AS age_band,
    coalesce(s.sex, 'unknown') AS sex,
    coalesce(s.channel, 'unknown') AS channel,
    ln(1 + s.income) AS log_income,
    s.income_missing::TINYINT AS income_missing
FROM base AS b
JOIN fact_monthly_holdings AS cur ON cur.customer_id = b.customer_id AND cur.month_index = b.feature_month
JOIN flags ON flags.customer_id = b.customer_id AND flags.month_index = b.feature_month
JOIN stg_holdings AS s ON s.customer_id = b.customer_id AND s.month_index = b.feature_month
JOIN dim_customer AS c ON c.customer_id = b.customer_id
JOIN history AS hist ON hist.customer_id = b.customer_id AND hist.feature_month = b.feature_month
LEFT JOIN fact_monthly_holdings AS p1 ON p1.customer_id = b.customer_id AND p1.month_index = b.feature_month - 1
LEFT JOIN fact_monthly_holdings AS p3 ON p3.customer_id = b.customer_id AND p3.month_index = b.feature_month - 3
LEFT JOIN next_adoptions AS a ON a.customer_id = b.customer_id AND a.month_index = b.label_month;

SELECT
    split,
    count(*) AS rows,
    count(DISTINCT customer_id) AS customers,
    sum(1 - holds_target_at_t) AS target_population,
    sum(adopts_target) AS target_adoptions,
    sum(adopts_any) AS any_adoptions
FROM propensity_dataset
GROUP BY split
ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'validation' THEN 2 ELSE 3 END;
