-- Next-product transitions: for each adoption of product B, the product(s) A the same
-- customer adopted in their most recent earlier adoption month. Adoptions in the same
-- month are not transitions between each other.

WITH adoptions AS (
    SELECT customer_id, month_index, product_code
    FROM product_events
    WHERE event_type = 'adoption'
),
adoption_months AS (
    SELECT
        customer_id,
        month_index,
        lag(month_index) OVER (PARTITION BY customer_id ORDER BY month_index) AS previous_adoption_month
    FROM (SELECT DISTINCT customer_id, month_index FROM adoptions)
),
transitions AS (
    SELECT
        a.product_code AS from_product,
        b.product_code AS to_product,
        b.customer_id,
        m.month_index - m.previous_adoption_month AS months_between
    FROM adoption_months AS m
    JOIN adoptions AS b ON b.customer_id = m.customer_id AND b.month_index = m.month_index
    JOIN adoptions AS a ON a.customer_id = m.customer_id AND a.month_index = m.previous_adoption_month
)
SELECT
    t.from_product,
    pf.product_name AS from_name,
    t.to_product,
    pt.product_name AS to_name,
    count(DISTINCT t.customer_id) AS customers,
    count(*) AS transitions,
    median(t.months_between) AS median_months_between,
    count(*) / sum(count(*)) OVER (PARTITION BY t.from_product) AS share_of_from
FROM transitions AS t
JOIN dim_product AS pf ON pf.product_code = t.from_product
JOIN dim_product AS pt ON pt.product_code = t.to_product
GROUP BY t.from_product, pf.product_name, t.to_product, pt.product_name
ORDER BY transitions DESC, t.from_product, t.to_product;
