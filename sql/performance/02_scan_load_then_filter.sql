-- Pair 2, baseline: "load everything, then filter". Every column of every monthly file is
-- copied into a table before the one month and two columns that are needed are selected.
-- (A plain SELECT * subquery would not show this: DuckDB pushes projections and filters
-- into it. The anti-pattern is materialising the full data first, as pandas code does.)
-- Output must equal 02_scan_pruned.sql. Parameters: $holdings_glob, $snapshot_date.

CREATE OR REPLACE TEMP TABLE perf_all_rows AS
SELECT * FROM read_parquet($holdings_glob, hive_partitioning = false);

SELECT count(*) AS customers, sum(ind_tjcr_fin_ult1) AS credit_card_holders
FROM perf_all_rows
WHERE fecha_dato = $snapshot_date::DATE;
