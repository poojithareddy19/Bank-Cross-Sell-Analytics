-- Pair 2, candidate: partition pruning + projection pushdown. The month filter is on the
-- hive partition column, so only the one month=YYYY-MM folder is opened, and only the
-- two columns used are read from it.
-- Output must equal 02_scan_load_then_filter.sql. Parameters: $holdings_glob, $month_label.

SELECT count(*) AS customers, sum(ind_tjcr_fin_ult1) AS credit_card_holders
FROM read_parquet($holdings_glob, hive_partitioning = true, hive_types = {'month': VARCHAR})
WHERE month = $month_label;
