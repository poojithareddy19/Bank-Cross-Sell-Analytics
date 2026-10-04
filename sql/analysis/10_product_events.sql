-- Product adoption and attrition events from one LAG window pass over the wide fact.
--   adoption:  flag 0 in the previous snapshot, 1 in this one
--   attrition: flag 1 in the previous snapshot, 0 in this one
-- The previous snapshot must be exactly one month earlier for that customer. Changes
-- across a gap are stored in product_changes_across_gaps and never counted as events.
-- Only rows where some flag changed are unpivoted, so the long form stays small.

CREATE OR REPLACE TEMP TABLE flag_changes AS
WITH deltas AS (
    SELECT
        customer_id,
        month_index,
        month_index - lag(month_index) OVER w AS months_since_previous,
        COLUMNS('^ind_.*_ult1$') - lag(COLUMNS('^ind_.*_ult1$')) OVER w
    FROM fact_monthly_holdings
    WINDOW w AS (PARTITION BY customer_id ORDER BY month_index)
),
changed_rows AS (
    SELECT *
    FROM deltas
    WHERE list_min([*COLUMNS('^ind_.*_ult1$')]) < 0 OR list_max([*COLUMNS('^ind_.*_ult1$')]) > 0
)
SELECT customer_id, month_index, months_since_previous, product_code, delta
FROM (UNPIVOT changed_rows ON COLUMNS('^ind_.*_ult1$') INTO NAME product_code VALUE delta)
WHERE delta <> 0;

CREATE OR REPLACE TABLE product_events AS
SELECT
    customer_id,
    month_index,
    product_code,
    CASE WHEN delta = 1 THEN 'adoption' ELSE 'attrition' END AS event_type
FROM flag_changes
WHERE months_since_previous = 1;

CREATE OR REPLACE TABLE product_changes_across_gaps AS
SELECT
    customer_id,
    month_index,
    months_since_previous,
    product_code,
    CASE WHEN delta = 1 THEN 'adoption' ELSE 'attrition' END AS change_type
FROM flag_changes
WHERE months_since_previous > 1;

DROP TABLE flag_changes;

-- Summary written to reports/analysis/10_product_events.csv
WITH events AS (
    SELECT
        month_index,
        product_code,
        count(*) FILTER (WHERE event_type = 'adoption') AS adoptions,
        count(*) FILTER (WHERE event_type = 'attrition') AS attritions
    FROM product_events
    GROUP BY month_index, product_code
),
gaps AS (
    SELECT month_index, product_code, count(*) AS changes_across_gaps
    FROM product_changes_across_gaps
    GROUP BY month_index, product_code
)
SELECT
    m.month_label,
    p.product_code,
    p.product_name,
    coalesce(e.adoptions, 0) AS adoptions,
    coalesce(e.attritions, 0) AS attritions,
    coalesce(g.changes_across_gaps, 0) AS changes_across_gaps
FROM dim_month AS m
CROSS JOIN dim_product AS p
LEFT JOIN events AS e ON e.month_index = m.month_index AND e.product_code = p.product_code
LEFT JOIN gaps AS g ON g.month_index = m.month_index AND g.product_code = p.product_code
WHERE m.month_index > 0
ORDER BY m.month_index, p.catalogue_order;
