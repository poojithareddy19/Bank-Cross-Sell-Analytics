-- Product ladder funnel. Ladder steps come from products.yaml via dim_product.ladder_step.
-- A customer reaches step 1 in the first month they hold any step-1 product, and step n
-- in the first month at or after reaching step n-1 in which they hold any step-n product.
-- So reached counts can never increase down the ladder. Conversion at step n =
-- reached(n) / reached(n-1); step 1 is measured against all customers in the group.
-- Groups: all customers, latest segment, and joining channel (top $top_channels, rest 'other').

WITH RECURSIVE held AS (
    SELECT DISTINCT u.customer_id, p.ladder_step AS step, u.month_index
    FROM (
        UNPIVOT (SELECT customer_id, month_index, COLUMNS('^ind_.*_ult1$') FROM fact_monthly_holdings)
        ON COLUMNS('^ind_.*_ult1$') INTO NAME product_code VALUE flag
    ) AS u
    JOIN dim_product AS p USING (product_code)
    WHERE u.flag = 1 AND p.ladder_step IS NOT NULL
),
reach(customer_id, step, reach_month) AS (
    SELECT customer_id, 1, min(month_index)
    FROM held
    WHERE step = 1
    GROUP BY customer_id
    UNION ALL
    SELECT r.customer_id, r.step + 1, min(h.month_index)
    FROM reach AS r
    JOIN held AS h ON h.customer_id = r.customer_id AND h.step = r.step + 1 AND h.month_index >= r.reach_month
    GROUP BY r.customer_id, r.step
),
steps AS (
    SELECT DISTINCT ladder_step AS step, ladder_step_name AS step_name
    FROM dim_product
    WHERE ladder_step IS NOT NULL
),
top_channels AS (
    SELECT channel
    FROM dim_customer
    WHERE channel IS NOT NULL
    GROUP BY channel
    ORDER BY count(*) DESC, channel
    LIMIT $top_channels
),
customers AS (
    SELECT
        c.customer_id,
        coalesce(c.latest_segment, 'unknown') AS segment,
        CASE
            WHEN c.channel IS NULL THEN 'unknown'
            WHEN c.channel IN (SELECT channel FROM top_channels) THEN c.channel
            ELSE 'other'
        END AS channel
    FROM dim_customer AS c
),
memberships AS (
    SELECT customer_id, 1 AS dimension_order, 'all' AS dimension, 'all' AS group_value FROM customers
    UNION ALL
    SELECT customer_id, 2, 'segment', segment FROM customers
    UNION ALL
    SELECT customer_id, 3, 'channel', channel FROM customers
),
counts AS (
    SELECT
        m.dimension_order,
        m.dimension,
        m.group_value,
        s.step,
        s.step_name,
        count(DISTINCT m.customer_id) AS customers,
        count(r.customer_id) AS reached
    FROM memberships AS m
    CROSS JOIN steps AS s
    LEFT JOIN reach AS r ON r.customer_id = m.customer_id AND r.step = s.step
    GROUP BY m.dimension_order, m.dimension, m.group_value, s.step, s.step_name
)
SELECT
    dimension,
    group_value,
    step,
    step_name,
    customers,
    coalesce(lag(reached) OVER w, customers) AS previous_reached,
    reached,
    CASE WHEN coalesce(lag(reached) OVER w, customers) > 0
         THEN reached / coalesce(lag(reached) OVER w, customers) END AS conversion_rate,
    reached / customers AS reached_share
FROM counts
WINDOW w AS (PARTITION BY dimension, group_value ORDER BY step)
ORDER BY dimension_order, group_value, step;
