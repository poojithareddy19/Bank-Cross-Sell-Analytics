# Project instructions

How to set up, run, understand and extend the Retail Bank Cross-Sell and Customer Journey
Analytics project. Results are summarised in the README and explained in
[PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md); this document is the practical guide.

## 1. Overview

A batch analytics pipeline over the Kaggle "Santander Product Recommendation" data: 17 monthly
snapshots of retail bank product holdings (13,647,309 customer-month rows, 956,645 customers). It
builds a DuckDB star schema, checks data quality, detects product adoption and attrition, analyses
journeys, cohorts and engagement, profiles query performance, and trains a calibrated next-month
credit card propensity model. Everything runs from one command line interface on a laptop.

## 2. Problem

Most bank customers hold one or two products. Product, marketing and CRM teams need trusted
answers to: who is engaged, how customers move from one product to the next, where they stall,
and who is likely to take up a specific product next month.

## 3. Goals

- Reproduce every number from the raw Kaggle file with one command.
- Define each metric once ([docs/metric_definitions.md](docs/metric_definitions.md)) and report
  rates with 95% intervals.
- Evaluate the propensity model on later months than it was trained on, against simple baselines,
  with leakage tests.
- Be honest about value: the data has no revenue, so value is a proxy and money figures rest on
  stated assumptions.

## 4. Requirements

| Requirement | Detail |
|---|---|
| Python | 3.12 (tested in CI); 3.11 is the declared minimum |
| RAM | 8 GB works with a lower DuckDB memory limit or a customer sample; the reference run used 15.3 GB with DuckDB capped at 4 GB |
| Disk | About 1 GB for the data after a run (175 MB Parquet, 783 MB DuckDB warehouse) plus room for DuckDB to spill during large queries; 5 GB free is comfortable |
| Kaggle account | Rules accepted for the competition, and either an API token or a manual download of `train_ver2.csv.zip` |
| OS | Windows, macOS or Linux; all paths use `pathlib` |

## 5. Technology

Python, SQL, DuckDB, Parquet via PyArrow, pandas, scikit-learn, XGBoost, statsmodels (Wilson
intervals), joblib, the Kaggle API, pytest, ruff and GitHub Actions. Exact versions are pinned in
`requirements.txt` and `requirements-dev.txt`.

## 6. Architecture

```text
Kaggle zip -> fetch -> month-partitioned Parquet -> warehouse (DuckDB star schema)
          -> quality -> analyze -> performance -> train -> reports/ and artifacts/
```

Python orchestrates and DuckDB computes. All SQL is in `.sql` files; Python binds named
parameters, adds statistics and writes reports. Only aggregates and the sampled model dataset are
loaded into pandas. There is no API or web service.

## 7. Data sources

One file from the Kaggle competition
[Santander Product Recommendation](https://www.kaggle.com/competitions/santander-product-recommendation):
`train_ver2.csv.zip` (224,672,878 bytes). Columns are described in
[docs/data_dictionary.md](docs/data_dictionary.md). The other files in the competition
(`test_ver2.csv.zip`, `sample_submission.csv.zip`) are not used.

## 8. Remote data and large file strategy

- The zip is downloaded once into `data/raw/`, either by `python -m xsell fetch` (Kaggle API) or by
  hand from the competition's data page.
- The CSV inside the zip is streamed straight into month-partitioned Parquet in
  `data/cache/holdings/` and never extracted. The run is validated (line count, months, row and
  customer counts read back) and recorded in `data/cache/manifest.json` with the zip's SHA-256.
- The zip is deleted after a successful conversion unless `--keep-zip` is passed. If the manifest
  matches the files on disk, `fetch` does nothing.
- `data/`, `*.duckdb`, `*.parquet`, `*.zip`, `artifacts/`, `.env` and `kaggle.json` are gitignored.
  Small generated reports in `reports/` are committed as evidence.

## 9. Environment setup

bash:

```bash
git clone https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics.git
cd Bank-Cross-Sell-Analytics
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt -e .
python -m xsell config
```

PowerShell:

```powershell
git clone https://github.com/poojithareddy19/Bank-Cross-Sell-Analytics.git
cd Bank-Cross-Sell-Analytics
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt -e .
python -m xsell config
```

Kaggle access, option A (API): accept the competition rules, create a token in your Kaggle
account settings and save it as `~/.kaggle/access_token` (the token text) or `~/.kaggle/kaggle.json`
(legacy key). On Windows `~` is `C:\Users\<you>`. Option B (no token): download
`train_ver2.csv.zip` from the competition's data page and place it in `data/raw/`.

Machine settings: set `duckdb.memory_limit` (about 40% of RAM) and `duckdb.threads` (logical
cores minus 2) in `config/config.yaml`, or override them with `XSELL_DUCKDB_MEMORY_LIMIT` and
`XSELL_THREADS` in the shell or a `.env` file (see `.env.example`).

## 10. Project structure

```text
config/            config.yaml (settings), products.yaml (catalogue and confirmed ladder)
sql/staging/       00_stg_holdings.sql: types, English names, cleaning
sql/warehouse/     dimensions and the wide fact table
sql/quality/       q01 to q08 checks, p01 to p03 profiles
sql/analysis/      10 to 17: events, penetration, transitions, ladder funnel, time to next product, cohorts, engagement, value proxy
sql/performance/   four baseline and candidate query pairs
sql/features/      20_propensity_features.sql
src/xsell/         config, db, data/ (download, convert, fetch), warehouse, quality, analysis,
                   performance, features, train, evaluate, pipeline, machine, reporting, cli
tests/             fixtures with planted answers, unit, leakage and end-to-end tests
docs/              data dictionary, metric definitions, stakeholder memo
reports/           generated reports from the real data
```

## 11. Implementation steps

The project was built one milestone at a time, each ending with passing tests and one commit:

| Milestone | Commit | What it added |
|---|---|---|
| 0 Scaffold | `d434c56` | Package, pinned dependencies, validated config, logging, CLI skeleton |
| 1 Data acquisition | `8f0c2ea` | Kaggle download, streamed zip to Parquet, manifest, synthetic fixture |
| 2 Warehouse and quality | `41d8f33` | Staging, star schema, quality checks and report, data dictionary |
| 3 Events and analytics | `342dc1d` | Adoption and attrition events, penetration, transitions, cohorts, engagement, value proxy |
| 4 Ladder funnel | `08f9641`, `3dd7d08` | Time-ordered funnel and time to next product; later, repeat adoptions and the confirmed ladder |
| 5 Query performance | `03a8e48` | Four timed query pairs with plans |
| 6 Propensity model | `d4d7c67` | Feature table, model selection, calibration, evaluation, leakage tests |
| 7 End-to-end and CI | `dd6f75e` | `all` command with a run record, end-to-end tests, GitHub Actions |
| 8 Quality review | `edc7e70` | Shared helpers, config instead of hard-coded values, `.env` support |
| 9 Documentation | `89049fd`, `62a3a48` | README, metric definitions, technical documentation, stakeholder memo |
| Real data run | `fc49454` | Reports from the first full run |
| 10 Instructions | this commit | This guide and interview preparation |

## 12. Important code components

| File | Role |
|---|---|
| `src/xsell/config.py` | Loads and validates both YAML files; every error names the setting |
| `src/xsell/data/convert.py` | Streams the zip into Parquet, validates, writes the manifest |
| `src/xsell/db.py` | DuckDB connection with memory and thread limits; runs `.sql` files with named parameters |
| `sql/analysis/10_product_events.sql` | One `LAG` pass for 24 products; consecutive months only; first-time vs repeat adoptions |
| `sql/analysis/13_ladder_funnel.sql` | Recursive CTE: each step reached in or after the month the previous step was reached |
| `src/xsell/analysis.py` | Runs the analysis SQL, adds Wilson intervals, writes the summary and ladder proposal |
| `src/xsell/performance.py` | Times query pairs, checks identical results, saves `EXPLAIN ANALYZE` plans |
| `sql/features/20_propensity_features.sql` | Features from months up to t, labels from t+1 |
| `src/xsell/train.py`, `src/xsell/evaluate.py` | Model selection, calibration, customer-level bootstrap, lift, capacity table |
| `src/xsell/pipeline.py` | Stage order and the `all` run record |

## 13. Model

Next-month credit card adoption among customers who do not hold it, trained on feature months
2015-04 to 2015-12, validated on 2016-01 to 2016-02 and tested once on 2016-03 to 2016-04, using a
deterministic 20% customer sample. Logistic regression and XGBoost, both class-weighted, are
compared on validation PR-AUC against a base-rate and a rule baseline; the winner is calibrated
(sigmoid) on validation. The target product, splits, sample share and assumptions are all set in
`config/config.yaml`. Details: README "Model details" and PROJECT_DOCUMENTATION.md section 9.

## 14. Application flow (CLI stages)

| Command | Reads | Writes |
|---|---|---|
| `python -m xsell config` | config files | summary in the log |
| `python -m xsell fetch` | Kaggle or `data/raw/train_ver2.csv.zip` | `data/cache/` |
| `python -m xsell warehouse` | Parquet cache | `data/warehouse.duckdb` |
| `python -m xsell quality` | warehouse | `reports/data_quality.md` |
| `python -m xsell analyze` | warehouse | `reports/customer_analytics.md`, `reports/analysis/`, `reports/ladder_proposal.md` |
| `python -m xsell performance` | warehouse, Parquet cache | `reports/performance.md`, `reports/performance/` |
| `python -m xsell train` | warehouse | `reports/model_report.{md,json}`, `artifacts/propensity_model.joblib` |
| `python -m xsell all` | everything above in order | plus `reports/pipeline_run.md` |

Options: `fetch --sample-customers 0.1` (fast development subset), `--keep-zip`, `--force`, and
`python -m xsell --config path/to/config.yaml <stage>`. A stage run before its inputs exist stops
with a message naming the command to run first.

## 15. Testing

```bash
python -m pytest
ruff check .
ruff format --check .
```

121 tests on synthetic data written in the raw Kaggle format: a 60-customer fixture with
hand-counted answers and a seeded 2,000-customer fixture for the model. They cover configuration,
conversion and corrupt inputs, credentials, every quality check and analysis query, the funnel,
identical results for performance pairs, leakage, the bootstrap metrics against scikit-learn,
determinism, and a full end-to-end CLI run. CI runs them on every push and never contacts Kaggle.

## 16. Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `Kaggle credentials not found` | No token where the Kaggle client looks | Save the token as `~/.kaggle/access_token` or `~/.kaggle/kaggle.json`. File Explorer may refuse a folder starting with a dot; create it with `mkdir` in a terminal. Or skip the token and download the zip by hand (section 9). |
| Token set as an environment variable but still not found | Variables set after the terminal or editor started are not visible to it | Open a new terminal, or save the token to the file instead |
| `Kaggle rejected the credentials (HTTP 401)` | Invalid or revoked token | Create a new token |
| `Kaggle refused the download (HTTP 403)` | Competition rules not accepted by the token's account | Accept the rules on the competition page while logged in as that account |
| fetch tries to download although the data is there | `data/raw/` holds the extracted `train_ver2.csv`, not the zip | The pipeline reads only `train_ver2.csv.zip`; put the zip in `data/raw/` and delete the extracted CSV |
| Zip not found after a manual download | The browser downloaded the whole competition bundle | Copy `train_ver2.csv.zip` out of `santander-product-recommendation.zip` |
| `is not a valid zip` | Incomplete or corrupt download | Delete it and download again |
| Out of memory, or the machine becomes unusable | DuckDB limit too high for the free RAM | Lower `XSELL_DUCKDB_MEMORY_LIMIT` and `XSELL_THREADS`; develop with `fetch --sample-customers 0.1`; lower `model.sample_share` |
| `could not open ... warehouse.duckdb` | Another process (a second run, a notebook, a DuckDB shell) holds the file | Close the other process and rerun |
| `No module named ...` from scikit-learn on Windows although it is installed | The folder path is too long (Windows 260-character limit) | Clone into a shorter path such as `C:\src\`, or enable long paths in Windows |
| Install fails or wheels are missing | Unsupported Python version | Use Python 3.12 (`py -3.12` on Windows, or `uv python install 3.12`) |
| `configuration error: ...` | A setting is missing or invalid | The message names the setting; fix it in `config/config.yaml` or `products.yaml` |
| `stage failed: ... run python -m xsell <stage> first` | Stages run out of order | Run the named stage, or use `python -m xsell all` |

## 17. Git workflow

- `main` holds the working pipeline; CI must pass on every push.
- One meaningful commit per finished unit of work, with a plain message describing the change.
- Before committing: `git status`, review the diff, run the tests and lint, and check that no data,
  DuckDB, Parquet, zip, model, `.env` or `kaggle.json` file is staged.
- Reports are regenerated by the pipeline, never edited by hand; commit them with the code commit
  they were produced from (the model report records that commit).

## 18. Running the project

Full run (about 26 minutes on the reference laptop, most of it the performance stage):

```bash
python -m xsell all
```

Faster development loop on a 10% customer subset. This rebuilds the cache from the zip, so keep it;
later, `python -m xsell fetch` without the option rebuilds the cache for all customers:

```bash
python -m xsell fetch --sample-customers 0.1 --keep-zip
python -m xsell warehouse
python -m xsell analyze
```

To change the target product, edit `model.target_product` in `config/config.yaml` and run
`python -m xsell train`. To change the ladder, edit `ladder` in `config/products.yaml` and run
`python -m xsell warehouse` then `python -m xsell analyze`.

## 19. Expected results

On the full data with the committed configuration (`reports/`):

| Output | Expected value |
|---|---|
| Rows, customers, months | 13,647,309, 956,645, 17 |
| Critical quality checks | all pass |
| Adoption events | 561,710 (51.7% repeats) |
| Ladder funnel | 65.1%, 18.6%, 22.0%, 31.7% conversion; 0.8% reach credit card |
| Selected model | XGBoost, validation PR-AUC 0.1628 |
| Test ROC-AUC, PR-AUC | 0.956, 0.166 |
| First-time adopters in top 10% | 63.1% |
| Total run time | about 1,583 s on the reference laptop (varies with hardware) |

Training is deterministic with seed 42; query timings vary between runs and machines.

## 20. Limitations

No revenue data; propensity is not uplift; 17 months of a Spanish bank in 2015 to 2016; training on
a 20% customer sample; the ladder simplifies real journeys; product flags switch off and on, so
raw adoption counts overstate new sales; comparisons are correlational.

## 21. Future improvements

A randomised targeting test for incremental adoption; a model trained only on first-time adoption
using the full customer base; uplift modelling; survival analysis for time to next product; a
scheduled monthly refresh with drift monitoring (population stability index).
