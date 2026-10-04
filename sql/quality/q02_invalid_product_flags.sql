-- severity: critical
-- description: A product flag holds a value other than 0 or 1.

SELECT
    customer_id,
    month_index,
    list_min([*COLUMNS('^ind_.*_ult1$')]) AS lowest_flag,
    list_max([*COLUMNS('^ind_.*_ult1$')]) AS highest_flag
FROM stg_holdings
WHERE list_min([*COLUMNS('^ind_.*_ult1$')]) < 0 OR list_max([*COLUMNS('^ind_.*_ult1$')]) > 1
ORDER BY customer_id, month_index;
