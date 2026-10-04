-- severity: profile
-- description: Total product flags filled from NULL with 0.

SELECT
    coalesce(sum(flags_filled_from_null), 0)::BIGINT AS flags_filled,
    count(*) FILTER (WHERE flags_filled_from_null > 0) AS rows_affected,
    count(*) AS total_rows
FROM stg_holdings;
