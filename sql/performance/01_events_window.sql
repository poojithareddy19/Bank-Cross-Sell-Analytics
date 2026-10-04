-- Pair 1, candidate: the same counts with one LAG window pass (as in
-- sql/analysis/10_product_events.sql). The table is read once and sorted by customer and
-- month inside the window operator; no hash table is built for a join.
-- Output must equal 01_events_self_join.sql.

WITH deltas AS (
    SELECT
        month_index - lag(month_index) OVER w AS months_since_previous,
        COLUMNS('^ind_.*_ult1$') - lag(COLUMNS('^ind_.*_ult1$')) OVER w
    FROM fact_monthly_holdings
    WINDOW w AS (PARTITION BY customer_id ORDER BY month_index)
),
changed AS (
    SELECT COLUMNS('^ind_.*_ult1$')
    FROM deltas
    WHERE months_since_previous = 1
      AND (list_min([*COLUMNS('^ind_.*_ult1$')]) < 0 OR list_max([*COLUMNS('^ind_.*_ult1$')]) > 0)
)
SELECT
    product_code,
    count(*) FILTER (WHERE delta = 1) AS adoptions,
    count(*) FILTER (WHERE delta = -1) AS attritions
FROM (UNPIVOT changed ON COLUMNS(*) INTO NAME product_code VALUE delta)
WHERE delta <> 0
GROUP BY product_code
ORDER BY product_code;
