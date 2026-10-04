# Interview preparation

Questions this project should prepare you for, with talking points built from what was actually
built and measured. The points are prompts, not scripts: rewrite each answer in your own words and
be ready to open the report that backs every number.

## Numbers to know

| Fact | Value | Source |
|---|---|---|
| Data | 13,647,309 customer-month rows, 956,645 customers, 17 months (Jan 2015 to May 2016), 24 products | `reports/data_quality.md` |
| Full run | 1,583 s (26 min) on a Ryzen 5 laptop, 15.3 GB RAM, DuckDB capped at 4 GB | `reports/pipeline_run.md` |
| Conversion memory | 635 MB peak, CSV never extracted | `data/cache/manifest.json` |
| Adoptions | 561,710, of which 51.7% repeats | `reports/customer_analytics.md` |
| Ladder funnel | 65.1% reach current account; 18.6%, 22.0%, 31.7% step conversion; 0.8% reach credit card | same |
| Weakest first step | university 8.6% vs individuals 26.5%; channel KHQ 3.1% vs about 29% for KAT and KFC | `reports/analysis/13_ladder_funnel.csv` |
| Engagement | 53.2% to 42.5% active; 0.7% with 0 products vs 84.3% with 2 | `reports/customer_analytics.md` |
| Model | XGBoost; test ROC-AUC 0.956, PR-AUC 0.166 vs 0.0167 rule and 0.0047 base rate; top-decile lift 8.55x | `reports/model_report.md` |
| First-time adopters | 252 of 1,688 test adopters; ROC-AUC 0.901; 63.1% in the top 10%; lift 6.31x | same |
| Query pairs | pruning 1,397x; materialised table 654x (pays back after 0.9 queries); cumulative window 8.9x; self-join beat `LAG` 2.1x | `reports/performance.md` |
| Tests | 121, CI on every push, synthetic data only | `tests/` |

## Project questions

**Why did you build it?**
- Filled a gap: SQL depth, customer journeys and cross-sell analytics in banking, on real data at a
  size that needs care on a laptop.
- Complements the churn project (retention) with acquisition of additional products.

**What problem does it solve?**
- Who is engaged, how customers move between products, where they stall, and who is likely to take
  a product next month, with one definition per metric and intervals on every rate.

**Why DuckDB, and why a wide fact table?**
- DuckDB: columnar and parallel, no server, reads Parquet directly; the whole warehouse builds in
  79 s on 13.6M rows.
- Wide fact: a long customer x month x product table would have 24 times the rows. Events come from
  one window pass over the wide table, and only rows with a change are unpivoted.

**How does the pipeline work end to end?**
- fetch (zip streamed to month-partitioned Parquet, validated, manifest) -> warehouse (staging and
  star schema) -> quality (8 checks, critical ones stop the run) -> analyze -> performance -> train.
  One CLI, `python -m xsell all`, writes every report and a run record.

**Biggest challenges, and how you solved them**
- Memory: streaming the CSV in 4 MB blocks with month buffers kept conversion at 635 MB; DuckDB runs
  with a 4 GB cap and a spill folder.
- Month gaps: 8,018 customers skip months; events count only when the previous row is exactly one
  month earlier, and changes across a gap are reported separately (2,369).
- Mixed types: padded numbers, `-999999` seniority, `indrel_1mes` spelled several ways, `NA` incomes;
  all cleaned in one tested SQL staging file.
- Rare target and a surprise in the data: half of all adoptions turned out to be products switching
  back on; I separated first-time from repeat adoptions and reported the model both ways.

**How did you evaluate?**
- Time-based split (train 2015-04 to 2015-12, validate 2016-01 to 2016-02, test 2016-03 to 2016-04,
  used once); PR-AUC for selection because positives are about 0.5%; calibration and Brier score;
  lift and capture; customer-level bootstrap intervals; results per test month (PR-AUC 0.180 and
  0.153).

**What would you improve?**
- Train a dedicated first-time adoption model on the full customer base; run a randomised targeting
  test; survival analysis for time to next product; scheduled refresh with drift monitoring.

**What happens if the download or the data fails?**
- Missing credentials, HTTP 401 and 403 each give a clear message; a corrupt zip, a wrong header or
  a line-count mismatch stops conversion before the cache is replaced; critical quality checks stop
  the pipeline after writing the report.

**How would you scale it?**
- Move the same SQL to BigQuery or Snowflake, load new months incrementally instead of rebuilding,
  manage the models in dbt, and keep the Python for statistics and modelling.

**How would you deploy it?**
- A scheduled monthly batch job: refresh data, score customers, write scores to a CRM table with a
  random holdout group. No API is needed for monthly campaign lists.

## Technical questions

### SQL

**`LAG` vs a self-join.** Both compare a row with the previous month. I expected `LAG` to win, but
on DuckDB the hash self-join on (customer, month - 1) was 2.1x faster, because the window operator
sorts every row while the hash join is an equality lookup run in parallel. For running totals the
window won 8.9x, because the range join produces n(n+1)/2 rows per customer. I kept `LAG` in the
analysis because one pass also sees the previous row across a gap. Lesson: measure.

**`PARTITION BY` vs `GROUP BY`.** `GROUP BY` collapses rows to one per group; `PARTITION BY` in a
window keeps every row and computes within the group, which is what event detection needs.

**Handling gaps in monthly data.** Compare each row with the customer's previous row and require the
month difference to be exactly 1; record other changes as changes across a gap.

**`UNPIVOT`.** Turns the 24 flag columns into (product, value) rows. I unpivot only rows where some
flag changed, and DuckDB's `COLUMNS()` lets one expression cover all 24 flags.

**Star schema vs one wide table.** Dimensions hold customer, product and month attributes once; the
fact holds the monthly measures. The fact itself is wide across products for performance.

**Partition pruning and projection pushdown.** Filtering on the hive partition column means DuckDB
opens only the `month=2016-05` folder, and it reads only the columns used: 0.034 s against 47.6 s
for loading everything first (`reports/performance.md`).

**Reading an `EXPLAIN ANALYZE` plan.** Read bottom up: scans (rows and columns read, filters pushed
down), joins (build and probe sizes), aggregates; the timings show where the query spends its time.
Plans for every pair are in `reports/performance/plans/`.

**How you validated data quality.** One SQL file per check returning violating rows, with severity
in its header: critical checks (duplicates, invalid flags, row reconciliation) stop the run; all
three passed on the real data.

### Statistics

**Wilson vs normal-approximation intervals.** The normal interval can fall outside 0 to 1 and is too
narrow for small groups or rates near 0 or 1; Wilson stays in range and behaves well there. Every
rate in the reports has one.

**Bootstrap intervals.** Resample and recompute the metric 1,000 times; the 2.5th and 97.5th
percentiles give the interval. I resampled customers, not rows, because one customer appears in
several months.

**Why cohort comparisons are correlational.** Cohorts differ in who joined and when (the July to
October 2015 cohorts are several times larger than the others), so differences in retention cannot
be attributed to anything without an experiment.

### Modelling

**Why PR-AUC for rare events.** With a 0.5% base rate, ROC-AUC can look excellent while precision
is poor; PR-AUC starts at the base rate for a useless model, so it shows the real gain (0.166 vs
0.0047).

**Calibration and the Brier score.** Class weighting inflates probabilities; sigmoid calibration on
validation moved the Brier score from 0.0791 to 0.0044. Calibrated probabilities make the expected
adopters in the capacity table meaningful.

**Leakage in time-based problems.** Features only from months up to t, labels from t+1. My test
changes every value after month t and checks that no feature moves, with a control test that a
change at t is detected.

**Why the split is by time.** The model will score future months; a random split would mix future
behaviour into training and overstate performance.

**Lift and capture rate.** Top-decile lift 8.55x: the top 10% adopt at 8.55 times the average; it
captures 85.5% of adopters. For first-time adopters only, 63.1% and 6.31x.

**Logistic regression odds ratios.** exp(coefficient), per standard deviation for scaled inputs.
Here they were dominated by sparse categories, so permutation importance was the better guide.

**Class weighting.** Weights the rare class up so the model does not ignore it; it distorts
probabilities, which calibration then corrects.

**Why propensity is not causal effect.** The model finds who is likely to adopt, including people
who would adopt anyway. Only a randomised holdout measures the incremental effect of contacting
them.

**The finding you are proudest of.** The headline model looked excellent (85.5% of adopters in the
top 10%), but 85% of adopters had held the card before. Splitting the results showed the real
cross-sell performance (63.1%, 6.31x lift) and changed the recommendation.

### Engineering

**Streaming a zip to Parquet in batches.** Open the zip member as a stream, parse with
`pyarrow.csv.open_csv`, split each block by month and write row groups per month; validate with an
independent line count before swapping the new cache in.

**Idempotent pipelines.** A manifest records file sizes and the zip hash; if it matches, fetch does
nothing, and every table is rebuilt with `CREATE OR REPLACE`.

**Fixture tests with known answers.** Synthetic data in the raw Kaggle format with planted events
(an adoption, an attrition, a repeat adoption, a month gap) and hand-counted expected results.

**CI without real data.** All 121 tests use the fixtures; GitHub Actions runs lint and tests on
every push and never contacts Kaggle.

## Resume

**Title:** Retail Bank Cross-Sell and Customer Journey Analytics

**Tech stack:** Python, SQL, DuckDB, Parquet (PyArrow), pandas, scikit-learn, XGBoost, statsmodels,
pytest, GitHub Actions

- Built a SQL-first customer analytics pipeline in DuckDB over 13.6M monthly bank holding records
  (956,645 customers, 24 products) with a star schema, window-function event detection and
  automated data quality checks, running end to end in 26 minutes on a laptop.
- Mapped product journeys and a 4-step product-ladder funnel with 95% Wilson intervals, showing
  that only 18.6% of customers who reach a current account go on to direct debit (8.6% in the
  university segment) and that 51.7% of recorded adoptions were products switching back on.
- Cut a monthly snapshot query from 47.6 s to 0.034 s with Parquet partition pruning and column
  projection, and per-query aggregation time 654x with a materialised table, documented with
  `EXPLAIN ANALYZE` profiles.
- Developed a calibrated next-month credit card propensity model with a time-based split and
  leakage tests, reaching test PR-AUC 0.166 against 0.017 for a rule-based baseline and 6.3x
  top-decile lift on first-time adopters.
