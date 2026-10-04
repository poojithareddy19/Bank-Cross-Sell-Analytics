-- Join-month cohort retention. Cohorts are customers whose join date (fecha_alta) falls
-- on or after the first snapshot month, so their first months are observed. Retention
-- at k = share of the cohort present in the snapshot k months after the join month AND
-- holding at least one product. Only observable k (join month + k <= last snapshot) appear.
-- Parameters: $first_month (first snapshot month, DATE).

WITH cohort_members AS (
    SELECT customer_id, join_month, date_diff('month', $first_month::DATE, join_month) AS join_month_index
    FROM dim_customer
    WHERE join_month >= $first_month::DATE
),
cohorts AS (
    SELECT join_month, join_month_index, count(*) AS cohort_size
    FROM cohort_members
    GROUP BY join_month, join_month_index
),
offsets AS (
    SELECT c.join_month, c.cohort_size, k.month_index AS months_since_join
    FROM cohorts AS c
    CROSS JOIN dim_month AS k
    WHERE c.join_month_index + k.month_index <= (SELECT max(month_index) FROM dim_month)
),
retained AS (
    SELECT
        m.join_month,
        f.month_index - m.join_month_index AS months_since_join,
        count(*) AS retained
    FROM cohort_members AS m
    JOIN fact_monthly_holdings AS f ON f.customer_id = m.customer_id
    WHERE f.month_index >= m.join_month_index AND f.n_products >= 1
    GROUP BY ALL
)
SELECT
    strftime(o.join_month, '%Y-%m') AS join_month,
    o.cohort_size,
    o.months_since_join,
    coalesce(r.retained, 0) AS retained,
    coalesce(r.retained, 0) / o.cohort_size AS retention_rate
FROM offsets AS o
LEFT JOIN retained AS r ON r.join_month = o.join_month AND r.months_since_join = o.months_since_join
ORDER BY o.join_month, o.months_since_join;
