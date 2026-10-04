# Retail Bank Cross-Sell and Customer Journey Analytics

[![CI](https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics/actions/workflows/ci.yml)

A SQL-first analytics pipeline over 17 monthly snapshots of real retail bank product holdings
(Santander, January 2015 to May 2016). It builds a DuckDB star schema, detects product adoption
and attrition with window functions, maps product journeys, cohorts and engagement, profiles
query performance, and trains a calibrated next-month cross-sell propensity model with a strict
time-based evaluation.

The full pipeline ran on all 13,647,309 customer-month rows (956,645 customers) in 26 minutes on a
15 GB laptop. Every number below comes from a file in [reports/](reports/); the one-page summary for
decision makers is [docs/stakeholder_memo.md](docs/stakeholder_memo.md).

## Findings

**Customers stall at the first product** ([customer_analytics.md](reports/customer_analytics.md)).
On the confirmed ladder, 65.1% of customers reach a current account, 18.6% of those add direct
debit, 22.0% of those the payroll package and 31.7% of those a credit card: 0.8% reach the top.
The first step is weakest for university-segment customers (8.6%) and for channels KHE (6.9%) and
KHQ (3.1%), against about 29% for channels KAT and KFC
([13_ladder_funnel.csv](reports/analysis/13_ladder_funnel.csv)).

**Half of all adoptions are products switching back on.** 290,392 of 561,710 adoption events
(51.7%) are products the customer had dropped earlier in the window, mostly payment products such
as direct debit, payroll, pension payments and credit card. Journeys and transitions therefore use
first-time adoptions only.

**Engagement follows product breadth and is falling.** The active share fell from 53.2% (January
2015) to 42.5% (May 2016). In May 2016, 0.7% of customers with no product were active, against
84.3% with two and 98.8% with three; 25.2% of customers held no product.

**The credit card propensity model** ([model_report.md](reports/model_report.md), test months
March and April 2016, 95% customer-level bootstrap intervals):

| Measure | All adopters | First-time adopters only |
|---|---|---|
| Adopters in the test sample | 1,688 | 252 |
| Base rate | 0.47% | 0.07% |
| ROC-AUC | 0.956 (0.953 to 0.959) | 0.901 |
| PR-AUC | 0.166 (0.151 to 0.184) | 0.006 |
| Share of adopters in the top 10% of scores | 85.5% (83.9% to 87.2%) | 63.1% |
| Top-decile lift | 8.55x (8.39x to 8.72x) | 6.31x |

85% of credit card adopters had held the card earlier, and those re-adoptions are what make the
overall numbers look so strong. The first-time column is the cross-sell result (point estimates).

**Query performance** ([performance.md](reports/performance.md)): partition pruning answered a
monthly snapshot 1,397x faster than loading everything first, and a materialised customer table
was 654x faster per query, paying back its build after 0.9 queries. A cumulative window beat a
range self-join 8.9x, but for this month-to-previous-month lookup DuckDB's hash self-join was 2.1x
faster than `LAG`. The report shows that result as measured.

## Problem statement

A retail bank sells many products, but most customers hold only one or two. Product, marketing and
CRM teams need to know who is engaged, how customers move from one product to the next, where they
stall, and who is most likely to take up a specific product next month, using metrics they can
trust.

## Objectives

- A reproducible pipeline from the raw Kaggle file to reports, runnable on a laptop.
- One precise definition per metric ([docs/metric_definitions.md](docs/metric_definitions.md)),
  with 95% intervals on every rate.
- A next-month propensity model for one product (credit card by default) that is calibrated,
  evaluated on later months than it was trained on, and compared with simple baselines.
- An honest view of value: the data has no revenue, so value is a labelled proxy and money figures
  rest on stated assumptions.

## Features

- Streamed conversion of the large CSV inside the Kaggle zip into month-partitioned Parquet,
  without extracting it, with a validated manifest.
- DuckDB star schema: staging, `dim_customer`, `dim_product`, `dim_month`, and a wide
  `fact_monthly_holdings`.
- Eight data quality checks (critical or warning) plus three profile tables.
- Product adoption and attrition events from a single `LAG` pass, counting consecutive months only.
- Penetration, next-product transitions, a time-ordered product ladder funnel, time to next product,
  join-month cohort retention, engagement and a customer value proxy.
- Four paired query comparisons with `EXPLAIN ANALYZE` plans, reported as measured.
- Logistic regression and XGBoost propensity models, chosen on validation PR-AUC, calibrated, and
  tested once with customer-level bootstrap intervals, lift, capture and per-month stability.
- Leakage tests that change every value after month t and check that no feature moves.

## Architecture

```text
Kaggle API (train_ver2.csv.zip)
        |  fetch: stream zip -> month-partitioned Parquet + manifest (zip deleted)
        v
data/cache/holdings/month=YYYY-MM/*.parquet
        |  warehouse: SQL in sql/staging and sql/warehouse
        v
DuckDB data/warehouse.duckdb
  stg_holdings -> dim_customer, dim_product, dim_month, fact_monthly_holdings
        |                |                 |                   |
     quality          analyze         performance            train
  sql/quality/    sql/analysis/    sql/performance/    sql/features/ + scikit-learn, XGBoost
        |                |                 |                   |
        v                v                 v                   v
  reports/data_quality.md   reports/customer_analytics.md   reports/performance.md
  reports/ladder_proposal.md   reports/analysis/*.csv   reports/model_report.{md,json}
  reports/pipeline_run.md (from `all`)   artifacts/propensity_model.joblib (not committed)
```

Python orchestrates and DuckDB computes: all SQL lives in `.sql` files, and only aggregates or the
sampled model dataset are pulled into pandas. There is no API, web service or Docker image; the
project is a batch pipeline driven by a command line interface.

## Technology stack

| Area | Tools |
|---|---|
| Language | Python 3.12 (tested in CI); 3.11 is the declared minimum |
| Storage and SQL | DuckDB, Parquet (PyArrow), SQL |
| Data access | Kaggle API |
| Modelling | scikit-learn, XGBoost, statsmodels (Wilson intervals), joblib |
| Quality | pytest, ruff, GitHub Actions |

Exact versions are pinned in `requirements.txt` and `requirements-dev.txt`.

## Project structure

```text
config/            config.yaml (paths, months, limits, model, assumptions), products.yaml (catalogue, ladder)
sql/staging/       typed, renamed staging table
sql/warehouse/     star schema
sql/quality/       one file per check, each returns its violating rows
sql/analysis/      events, penetration, transitions, ladder funnel, time to next product, cohorts, engagement, value proxy
sql/performance/   paired baseline and candidate queries
sql/features/      propensity feature table
src/xsell/         pipeline code (config, db, data/, warehouse, quality, analysis, performance, features, train, evaluate, pipeline, cli)
tests/             synthetic fixtures with planted answers, unit, leakage and end-to-end tests
docs/              data dictionary, metric definitions
reports/           generated reports (small, committed after the real run)
```

## Data source

Kaggle competition [Santander Product Recommendation](https://www.kaggle.com/competitions/santander-product-recommendation)
(2016), file `train_ver2.csv.zip`: one row per customer per month, 24 customer attributes (Spanish
column names) and 24 product flags; the converter checks the header for exactly these columns.
Column meanings and cleaning rules are in [docs/data_dictionary.md](docs/data_dictionary.md).

Verified on the first run (`data/cache/manifest.json`, [data_quality.md](reports/data_quality.md)):

| Fact | Value |
|---|---|
| Zip | `train_ver2.csv.zip`, 224,672,878 bytes |
| CSV inside the zip | `train_ver2.csv`, 13,647,309 data lines, streamed and never extracted |
| Rows | 13,647,309 customer-month rows |
| Customers | 956,645 |
| Months | 17 snapshots, 2015-01-28 to 2016-05-28 |
| Parquet cache | 182.5 MB, peak memory 635 MB during conversion |
| Data quality | all critical checks pass; 760 impossible-age rows, 1,123 incomes above 10M EUR, 8,018 customers with month gaps, 16,063 rows with NULL flags filled, 7,031 customers without a join date |

The data must be downloaded by each user under the competition rules and is never committed to
this repository.

## Data loading approach

1. **Download once** with the Kaggle API, only the one file needed, into `data/raw/`.
2. **Stream-convert** the CSV out of the zip with `pyarrow.csv.open_csv` in 4 MB blocks, split each
   block by month and write zstd Parquet row groups to `data/cache/holdings/month=YYYY-MM/`. Peak
   memory on the real file was 635 MB.
3. **Validate**: the CSV line count must equal the parsed rows, the months must match the config,
   and the Parquet is read back for row and customer counts. Results go to
   `data/cache/manifest.json` with file sizes and the zip's SHA-256.
4. **Delete the zip** after a successful conversion (keep it with `--keep-zip`).
5. **Idempotent**: if the manifest matches the files on disk, `fetch` does nothing.
6. **Optional dev subset**: `--sample-customers 0.1` keeps a deterministic 10% of customers.

`data/`, `*.duckdb`, `*.parquet`, `*.zip` and `artifacts/` are gitignored.

## Installation

Python 3.12 is recommended. With bash:

```bash
git clone https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics.git
cd Bank-Cross-Sell-Analytics
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -e .
```

With PowerShell:

```powershell
git clone https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics.git
cd Bank-Cross-Sell-Analytics
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -e .
```

Kaggle access (needed only for `fetch`): accept the competition rules on the competition page,
create an API token in your Kaggle account settings and configure it as the Kaggle documentation
describes. Credentials are read by the Kaggle client, never by this project's config.

## Configuration

All settings are in [config/config.yaml](config/config.yaml) and
[config/products.yaml](config/products.yaml); nothing is hard-coded. Key settings:

| Setting | Purpose |
|---|---|
| `duckdb.memory_limit`, `duckdb.threads` | Keep DuckDB within the laptop's RAM and cores (about 40% of RAM, cores minus 2) |
| `months.first`, `months.last` | Snapshot range |
| `quality.*` | Plausible age and income bounds |
| `analysis.top_channels`, `analysis.timing_runs` | Channel grouping, timed runs per query |
| `model.target_product`, `model.splits`, `model.sample_share` | Target, time-based split, customer sample |
| `assumptions.*` | ASSUMED margin and contact cost for the value table |
| `ladder` (products.yaml) | Confirmed product ladder steps; the funnel is skipped while empty |

`XSELL_DATA_DIR`, `XSELL_DUCKDB_MEMORY_LIMIT` and `XSELL_THREADS` override the config from the
shell or from a `.env` file (see `.env.example`); shell values win.

## Usage

Each stage can be run on its own; `all` runs them in order and records the run in
`reports/pipeline_run.md`.

```bash
python -m xsell config        # validate the configuration and print a summary
python -m xsell fetch         # download (if needed) and build the Parquet cache
python -m xsell warehouse     # staging table and star schema
python -m xsell quality       # data quality checks -> reports/data_quality.md
python -m xsell analyze       # SQL analyses -> reports/analysis/, customer_analytics.md, ladder_proposal.md
python -m xsell performance   # paired query timings -> reports/performance.md
python -m xsell train         # features, model, evaluation -> reports/model_report.md
python -m xsell all           # everything above
```

Useful options: `fetch --sample-customers 0.1` (fast development subset), `fetch --keep-zip`,
`fetch --force`, and a different config with `python -m xsell --config path/to/config.yaml <stage>`.
Commands are the same in PowerShell.

## Model details

- **Target:** the customer does not hold the credit card at month t and holds it at t+1
  (configurable). If its training base rate is below 0.2%, the target becomes "adopts any new
  product next month" and the report says why.
- **Features** (all from months up to and including t): products held, product count and its
  1- and 3-month change, months since first observed, seniority, adoptions and attritions in the
  last 3 months, activity, segment, age band, income (log) with a missing flag, joining channel (top 10
  plus other), sex, relation type and new-customer flag.
- **Split by time, no shuffling:** train on feature months 2015-04 to 2015-12, validate on 2016-01
  to 2016-02 (model choice, calibration, threshold), test once on 2016-03 to 2016-04.
- **Sampling:** a deterministic 20% customer sample (integer hash of the customer id), the same
  for every split.
- **Candidates:** class-weighted logistic regression and XGBoost; selection by validation PR-AUC.
- **Baselines:** the base rate, and a rule (active customers with payroll and 3 or more products).
- **Calibration:** sigmoid, fitted on validation; Brier score and a calibration table are reported.
- **Leakage guards:** features are built only from months up to t; tests check that feature months
  precede label months, that the target's own flag is not a feature, that changing every value
  after t leaves the features unchanged, and that preprocessing is fitted on training rows only.

## Evaluation results

From [reports/model_report.md](reports/model_report.md). Training used a deterministic 20% customer
sample: 1,360,046 training rows (7,852 adopters), 352,758 validation rows and 356,058 test rows.

**Model selection on validation (PR-AUC):** XGBoost 0.1628, logistic regression 0.0931, rule
baseline 0.0195, base rate 0.0048. XGBoost was selected and beats both baselines. Sigmoid calibration on
validation brought the Brier score from 0.0791 to 0.0044.

**Test months, scored once:** see the table under [Findings](#findings). The model was stable
across the two test months (PR-AUC 0.180 and 0.153). At the operating threshold chosen on
validation (top 10%), 9.9% of test rows were selected, with precision 0.041 and recall 0.855.

**Pipeline run** ([reports/pipeline_run.md](reports/pipeline_run.md)): 1,583 s in total (fetch 171,
warehouse 79, quality 7, analyze 15, performance 1,044, train 267) on an AMD Ryzen 5 5500U, 12
logical cores, 15.3 GB RAM, with DuckDB limited to 4 GB and 10 threads; peak process memory
4,249 MB.

## Testing

```bash
python -m pytest
ruff check .
ruff format --check .
```

The tests run on two synthetic datasets written in the raw Kaggle format: a 60-customer fixture
with hand-counted answers (planted adoptions, an attrition, a month gap, missing income, a
`-999999` seniority, mixed codes, an impossible age), and a seeded 2,000-customer fixture with a
learnable credit-card signal for the model. They cover configuration errors, conversion and
corrupt zips, missing credentials, every quality check, every analysis query, the ladder funnel,
identical results for every performance pair, leakage, training determinism and an end-to-end run
of `python -m xsell all`. GitHub Actions runs lint and the tests on every push; it never contacts
Kaggle.

## Limitations

- No revenue data: value is a proxy, and money figures rest on assumed margin and contact cost.
- Product flags switch off and on (51.7% of adoptions are repeats), so raw adoption counts
  overstate new sales; journeys use first-time adoptions, and the model report splits first-time
  from re-adoption.
- The logistic regression odds ratios are dominated by sparse categories (single channels,
  relation types), so they are a weak guide; XGBoost permutation importance is the better one.
- Propensity is not uplift: the model ranks likely adopters, including those who would adopt
  anyway; measuring a campaign's effect needs a randomised experiment.
- Only 17 months of history, from a Spanish bank in 2015 to 2016 (not the UK market).
- The model is trained on a 20% customer sample to fit laptop memory.
- The product ladder is a simplification of real customer journeys.
- Cohort and segment comparisons are descriptive and correlational, not causal.

## Future improvements

- A randomised targeting experiment to measure incremental adoption.
- Uplift modelling once experiment data exists.
- Survival analysis for time to next product, which handles customers who have not adopted yet.
- A scheduled monthly refresh with drift monitoring (population stability index).

## GitHub usage

- `main` holds the working pipeline; every push runs [CI](.github/workflows/ci.yml) (ruff and
  pytest on Ubuntu with Python 3.12, synthetic data only).
- Generated reports under `reports/` are small and committed as evidence of results; data,
  DuckDB files, Parquet, zips and model artifacts are not.

## License

Apache License 2.0, see [LICENSE](LICENSE).
