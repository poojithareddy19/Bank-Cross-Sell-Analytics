# Metric definitions

Every metric in the reports has exactly one definition, listed here with the file that computes it.
Month t is a monthly snapshot; "the previous snapshot" is the same customer's row one calendar month
earlier.

## Customer and product basics

| Metric | Definition | Computed in |
|---|---|---|
| Customer present | The customer has a row in that month's snapshot. | `fact_monthly_holdings` |
| Holder | Customer with the product's flag = 1 in that month. NULL flags (only in `ind_nomina_ult1` and `ind_nom_pens_ult1`) count as 0 and are reported in the data quality report. | `sql/staging/00_stg_holdings.sql` |
| Products held | Sum of the 24 product flags in that month (`n_products`). | `sql/warehouse/04_fact_monthly_holdings.sql` |
| Penetration | Holders / customers present, per month and product. | `sql/analysis/11_product_penetration.sql` |
| Active customer | `ind_actividad_cliente` = 1 in that month (`is_active`). | staging |
| Engagement rate | Active customers / customers present. The monthly trend uses every month; breakdowns use only the latest month, so each customer counts once. | `sql/analysis/16_engagement.sql` |

## Product events

| Metric | Definition | Computed in |
|---|---|---|
| Adoption event | Flag = 0 in the previous snapshot and 1 in month t, where the previous snapshot is exactly one month earlier for that customer. | `sql/analysis/10_product_events.sql` |
| Attrition event | Flag = 1 in the previous snapshot and 0 in month t, consecutive months only. | same |
| Change across a gap | A flag change where the customer's previous row is more than one month earlier. Stored in `product_changes_across_gaps` and reported, never counted as an event. | same |
| First-time adoption | An adoption of a product the customer had not dropped earlier in the window. | same |
| Repeat adoption | An adoption of a product the customer dropped earlier in the window (an attrition, or a 1 to 0 change across a gap). Holding a product earlier but not in the previous month implies such a drop, so the rule is exact. Payment-type products (direct debit, payroll, pension payments, credit card) switch off and on often, so repeats are common for them. | same |
| First month | A customer's first observed month has no previous snapshot, so products held then are not adoptions. | same |
| Next-product transition A to B | For each first-time adoption of B, the product(s) A first-adopted in the same customer's most recent earlier first-time adoption month. Repeat adoptions are excluded, so there are no A to A transitions; adoptions in the same month are not transitions between each other. `share_of_from` = transitions A to B / all transitions from A. | `sql/analysis/12_next_product_transitions.sql` |

## Journeys and cohorts

| Metric | Definition | Computed in |
|---|---|---|
| Ladder step reached | Ladder steps are defined in `config/products.yaml`; each step lists one or more products. A customer reaches step 1 in the first month they hold any step-1 product, and step n in the first month at or after reaching step n-1 in which they hold any step-n product. Reached counts can therefore never increase down the ladder. | `sql/analysis/13_ladder_funnel.sql` |
| Ladder conversion | Reached(step n) / reached(step n-1); step 1 is measured against all customers in the group. Groups: all customers, latest segment, joining channel (top `analysis.top_channels`, the rest "other"). | same |
| Time to next product | Months between a customer's first observed snapshot and their first first-time adoption (repeat adoptions are not a next product). Customers with no adoption in the window are not in the distribution (right-censored); the report states how many adopted at all. For customers already present in the first snapshot this is time since observation started, not time since joining the bank. | `sql/analysis/14_time_to_next_product.sql` |
| Join cohort | Month of `fecha_alta` (earliest reported join date), only for customers who joined on or after the first snapshot month, so their first months are observed. Customers without a join date are excluded and counted in the data quality report. | `sql/analysis/15_join_cohort_retention.sql` |
| Cohort retention at k | Share of the cohort present in the snapshot k months after the join month and holding at least one product. Only k values that can be observed (join month + k up to the last snapshot) are reported. | same |
| Customer value proxy | Product families held, income band and activity in the latest month. The data has no revenue, so this is a proxy for relationship breadth, not value. Income bands are quartiles of known income among customers present in the latest month; unknown income is "missing". | `sql/analysis/17_customer_value_proxy.sql` |

## Bands

| Band | Values | Source column |
|---|---|---|
| Tenure band | 00-11, 12-35, 36-119, 120+ months, unknown | `seniority_months` (`antiguedad`) |
| Products held band | 0, 1, 2, 3, 4+ | `n_products` |
| Product families held | 0, 1, 2, 3+ (families from `config/products.yaml`) | product flags |
| Age band (model feature) | under 25, 25-34, 35-44, 45-54, 55-64, 65+, unknown | `age` at month t |

## Uncertainty

| Metric | Definition | Computed in |
|---|---|---|
| Wilson interval | 95% Wilson score interval for every reported rate (penetration, engagement, retention, ladder conversion), from statsmodels `proportion_confint(method="wilson")`. Preferred over the normal approximation because it stays inside 0 to 1 and behaves well for small groups and rates near 0 or 1. | `src/xsell/analysis.py` |
| Bootstrap interval | 95% percentile interval from 1,000 (`model.bootstrap_resamples`) customer-level resamples of the test set: customers, not rows, are drawn with replacement, because one customer contributes rows in several months. | `src/xsell/evaluate.py` |

## Propensity model

| Metric | Definition | Computed in |
|---|---|---|
| Target | `adopts_target`: the customer does not hold `model.target_product` at t and holds it at t+1. Population: customers present at t and t+1 who do not hold the product at t. If the training base rate is below `model.min_base_rate`, the target becomes `adopts_any` (at least one adoption event at t+1, population: customers present at t and t+1) and the report records why. | `sql/features/20_propensity_features.sql`, `src/xsell/train.py` |
| Base rate | Share of the population with label 1 in a split. | `src/xsell/train.py` |
| First-time vs re-adoption split | Test metrics computed separately for customers who never held the target product before t (first-time adoption, the cross-sell result) and for customers who held it in an earlier month (re-adoption). Uses `held_target_before_t`, a reporting column built from months before t; it is never a model feature. | same |
| ROC-AUC | Probability that a random adopter is scored above a random non-adopter (ties count half). | `src/xsell/evaluate.py` |
| PR-AUC | Average precision: precision averaged over the recall levels reached at each distinct score threshold (scikit-learn definition). The selection metric, because adopters are rare and a no-skill model scores the base rate. | same |
| Brier score | Mean squared difference between the predicted probability and the 0/1 label. Lower is better; reported before and after calibration. | same |
| Calibration table | Ten equal-size bins of predicted probability: mean predicted probability against the observed adoption rate. Equal-size bins are used because with a base rate of a few percent, equal-width bins put almost every row in the first bin. | same |
| Decile lift | Adoption rate in a score decile / overall adoption rate. Decile 1 has the highest scores. | same |
| Capture at top k% | Share of all adopters found among the top k% of rows by score (k = 5, 10, 20). | same |
| Operating threshold | The calibrated probability at the top 10% of validation rows, chosen on validation and applied unchanged to the test months. | `src/xsell/train.py` |
| Rule baseline | Score 1 for active customers holding `model.rule_payroll_product` with at least `model.rule_min_products` products, else 0. The model must beat it and the base rate on validation PR-AUC. | same |
| Expected adopters (capacity table) | Sum of calibrated probabilities among the contacted customers. Observed adopters in the test months are shown next to it. | `src/xsell/evaluate.py` |
| Assumed value | Expected adopters x `assumptions.margin_per_adopter_eur` minus customers contacted x `assumptions.contact_cost_eur`. Both inputs are ASSUMPTIONS from config, not data. Propensity is not uplift: the value includes customers who would adopt anyway, so a randomised experiment is needed to measure the incremental effect. | same |

## Data quality

| Severity | Meaning |
|---|---|
| critical | The pipeline stops after writing the report (duplicate customer-months, flags other than 0/1, star schema row counts that do not reconcile with staging). |
| warning | Reported and handled (impossible ages, income out of range, month gaps, flags filled from NULL, missing join dates). |
| profile | Descriptive tables (NULLs per column, total flags filled, table row counts). |

Thresholds (`quality.age_min`, `quality.age_max`, `quality.income_max_eur`) come from `config/config.yaml`.

## Query performance

| Metric | Definition | Computed in |
|---|---|---|
| Median run time | One warm-up run, then `analysis.timing_runs` timed runs per variant; the median is reported. | `src/xsell/performance.py` |
| Faster, slower / faster | Which variant had the lower median, and the ratio of the two medians. Reported as measured, including when the baseline wins. | same |
| Break-even queries | One-off build time / (baseline median - candidate median): after how many queries a materialised table pays for itself. | same |
