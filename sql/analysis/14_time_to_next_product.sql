-- Time to next product = months between a customer's first observed snapshot and their
-- first adoption event. Customers with no adoption in the window are not in this
-- distribution (right-censored); the summary reports how many adopted at all.
-- Customers first seen in the first snapshot were already customers, so this is time
-- since observation started, not time since joining the bank.

WITH first_adoption AS (
    SELECT customer_id, min(month_index) AS first_adoption_month
    FROM product_events
    WHERE event_type = 'adoption'
    GROUP BY customer_id
),
times AS (
    SELECT f.first_adoption_month - c.first_seen_month_index AS months_to_first_adoption
    FROM dim_customer AS c
    JOIN first_adoption AS f USING (customer_id)
)
SELECT
    months_to_first_adoption,
    count(*) AS customers,
    count(*) / sum(count(*)) OVER () AS share_of_adopters,
    sum(count(*)) OVER (ORDER BY months_to_first_adoption) / sum(count(*)) OVER () AS cumulative_share,
    (SELECT median(months_to_first_adoption) FROM times) AS median_months,
    (SELECT count(*) FROM dim_customer) AS customers_observed
FROM times
GROUP BY months_to_first_adoption
ORDER BY months_to_first_adoption;
