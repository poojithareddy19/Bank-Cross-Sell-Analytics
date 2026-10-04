-- severity: profile
-- description: NULL values per staging column after cleaning.

WITH nulls AS (
    UNPIVOT (SELECT count(*) - count(COLUMNS(*)) FROM stg_holdings)
    ON COLUMNS(*) INTO NAME column_name VALUE null_rows
)
SELECT
    column_name,
    null_rows,
    round(null_rows / (SELECT count(*) FROM stg_holdings), 6) AS null_share
FROM nulls
WHERE null_rows > 0
ORDER BY null_rows DESC, column_name;
