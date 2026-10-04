# Propensity model report

Generated 2026-10-04T20:55:48+00:00 from commit `3dd7d08` by `python -m xsell train`.

## Target

Label: `adopts_target`. Population: customers present at t and t+1 who do not hold the target product at t. Choice: training base rate of ind_tjcr_fin_ult1 adoption is 0.5773%, at or above model.min_base_rate 0.20%.

## Data

Deterministic customer sample: 20% of customers (same sample for every split). Time-based split, no shuffling; every feature uses months up to the feature month t only.

| split | feature months | rows | customers | adopters | base rate |
|---|---|---|---|---|---|
| train | 2015-04 to 2015-12 | 1,360,046 | 179,888 | 7,852 | 0.58% |
| validation | 2016-01 to 2016-02 | 352,758 | 178,010 | 1,692 | 0.48% |
| test | 2016-03 to 2016-04 | 356,058 | 179,361 | 1,688 | 0.47% |

## Model selection (validation)

| model | pr_auc | roc_auc | brier |
|---|---|---|---|
| logistic_regression | 0.0931 | 0.9483 | 0.0898 |
| xgboost | 0.1628 | 0.9576 | 0.0791 |
| baseline: base_rate | 0.0048 | 0.5 | 0.0048 |
| baseline: rule | 0.0195 | 0.6498 |  |

Selected: **xgboost** (highest validation PR-AUC). It beats both baselines on validation PR-AUC. Rule baseline: active customers holding ind_nomina_ult1 with 3 or more products.

Calibration (sigmoid, fitted on validation): Brier 0.0791 before, 0.0044 after (measured on the validation rows used to fit it). Operating point chosen on validation: top 10%, calibrated probability threshold 0.0061.

## Test results (scored once)

95% intervals from a customer-level bootstrap with 1,000 resamples (0 skipped for having one class).

| metric | estimate (95% CI) |
|---|---|
| roc_auc | 0.9561 (0.9529 to 0.9594) |
| pr_auc | 0.1658 (0.1506 to 0.1843) |
| brier | 0.0044 (0.0042 to 0.0046) |
| capture_top_5pct | 0.7352 (0.7144 to 0.7577) |
| top_decile_lift | 8.5545 (8.3861 to 8.7220) |
| capture_top_10pct | 0.8555 (0.8386 to 0.8722) |
| capture_top_20pct | 0.9556 (0.9455 to 0.9648) |
| baseline base_rate pr_auc | 0.0047 |
| baseline rule pr_auc | 0.0167 |

At the validation threshold: 9.9% of test rows selected, precision 0.041, recall 0.855.

### First-time adoption versus re-adoption

The same test scores, split by whether the customer held the target product in an earlier month of the window. Re-adoptions (a product that switched off and on again) are much easier to predict; the first-time row is the cross-sell result.

| segment | rows | positives | base_rate | roc_auc | pr_auc | brier | capture_top_5pct | top_decile_lift | capture_top_10pct | capture_top_20pct |
|---|---|---|---|---|---|---|---|---|---|---|
| never held before t (first-time) | 346,818 | 252 | 0.0007 | 0.9013 | 0.0061 | 0.0009 | 0.4048 | 6.3095 | 0.631 | 0.8571 |
| held earlier (re-adoption) | 9,240 | 1,436 | 0.1554 | 0.6783 | 0.2848 | 0.1361 | 0.1295 | 2.1031 | 0.2103 | 0.3684 |

### Stability by test month

| label_month | rows | positives | base_rate | roc_auc | pr_auc | brier | capture_top_5pct | top_decile_lift | capture_top_10pct | capture_top_20pct |
|---|---|---|---|---|---|---|---|---|---|---|
| 2016-04 | 177,786 | 861 | 0.0048 | 0.9579 | 0.1801 | 0.0044 | 0.7445 | 8.5947 | 0.8595 | 0.9593 |
| 2016-05 | 178,272 | 827 | 0.0046 | 0.9544 | 0.1526 | 0.0043 | 0.7267 | 8.5369 | 0.8537 | 0.9516 |

### Lift by decile

| decile | rows | adopters | adoption_rate | lift | cumulative_capture | cumulative_lift |
|---|---|---|---|---|---|---|
| 1 | 35,606 | 1,444 | 0.0406 | 8.5545 | 0.8555 | 8.5545 |
| 2 | 35,606 | 169 | 0.0047 | 1.0012 | 0.9556 | 4.7778 |
| 3 | 35,606 | 51 | 0.0014 | 0.3021 | 0.9858 | 3.2859 |
| 4 | 35,606 | 19 | 0.0005 | 0.1126 | 0.997 | 2.4926 |
| 5 | 35,606 | 1 | 0 | 0.0059 | 0.9976 | 1.9952 |
| 6 | 35,606 | 3 | 0.0001 | 0.0178 | 0.9994 | 1.6657 |
| 7 | 35,606 | 1 | 0 | 0.0059 | 1 | 1.4286 |
| 8 | 35,606 | 0 | 0 | 0 | 1 | 1.25 |
| 9 | 35,605 | 0 | 0 | 0 | 1 | 1.1111 |
| 10 | 35,605 | 0 | 0 | 0 | 1 | 1 |

### Calibration (equal-size score bins)

| bin | rows | mean_predicted | observed_rate |
|---|---|---|---|
| 1 | 35,606 | 0.0001 | 0 |
| 2 | 35,606 | 0.0001 | 0 |
| 3 | 35,606 | 0.0001 | 0 |
| 4 | 35,605 | 0.0001 | 0 |
| 5 | 35,606 | 0.0001 | 0.0001 |
| 6 | 35,606 | 0.0002 | 0 |
| 7 | 35,605 | 0.0002 | 0.0005 |
| 8 | 35,606 | 0.0006 | 0.0014 |
| 9 | 35,606 | 0.0025 | 0.0047 |
| 10 | 35,606 | 0.0418 | 0.0406 |

## Interpretation

Logistic regression odds ratios (numeric inputs standardised, so per one standard deviation):

| feature | log_odds | odds_ratio |
|---|---|---|
| channel_KHQ | -5.5256 | 0.004 |
| channel_unknown | -4.6817 | 0.0093 |
| ind_ctju_fin_ult1 | -4.5571 | 0.0105 |
| relation_type_I | -2.9672 | 0.0514 |
| relation_type_R | 2.9031 | 18.2311 |
| segment_unknown | 2.1714 | 8.7707 |
| channel_KFA | 1.6528 | 5.2216 |
| channel_KFC | 1.4674 | 4.3379 |
| channel_KAT | 1.457 | 4.293 |
| channel_other | 1.4471 | 4.2508 |
| ind_pres_fin_ult1 | -1.1243 | 0.3249 |
| segment_01 - TOP | -1.0921 | 0.3355 |
| relation_type_P | -1.0867 | 0.3373 |
| segment_02 - PARTICULARES | -1.0238 | 0.3592 |
| segment_03 - UNIVERSITARIO | -1.005 | 0.3661 |

Permutation importance of the selected model on 50,000 validation rows (drop in PR-AUC when a column is shuffled):

| feature | pr_auc_drop_mean | pr_auc_drop_std |
|---|---|---|
| relation_type | 0.0645 | 0.0115 |
| n_products | 0.0583 | 0.0074 |
| attritions_last_3m | 0.0546 | 0.0046 |
| n_products_change_1m | 0.0488 | 0.0047 |
| active_months_last_3m | 0.0384 | 0.0125 |
| n_products_change_3m | 0.0231 | 0.0088 |
| ind_recibo_ult1 | 0.0197 | 0.0038 |
| ind_cno_fin_ult1 | 0.0158 | 0.0024 |
| seniority_months | 0.013 | 0.0059 |
| channel | 0.0129 | 0.0064 |
| ind_nom_pens_ult1 | 0.012 | 0.0012 |
| age_band | 0.0099 | 0.0049 |
| ind_cco_fin_ult1 | 0.0088 | 0.0018 |
| log_income | 0.0046 | 0.0025 |
| ind_ctop_fin_ult1 | 0.0037 | 0.0019 |

## Contact capacity (test months)

Expected adopters are the sum of calibrated probabilities. assumed_value_eur uses ASSUMED values from config: margin 100.00 EUR per adopter, contact cost 1.00 EUR. Assumed values from config/config.yaml, not data. The source has no revenue fields.

| share_contacted | customers_contacted | expected_adopters | observed_adopters | precision | capture | assumed_value_eur |
|---|---|---|---|---|---|---|
| 0.05 | 17,803 | 1,222.319 | 1,241 | 0.0697 | 0.7352 | 104,428.9044 |
| 0.1 | 35,606 | 1,487.3956 | 1,444 | 0.0406 | 0.8555 | 113,133.5599 |
| 0.2 | 71,212 | 1,577.7954 | 1,613 | 0.0227 | 0.9556 | 86,567.5377 |
| 0.3 | 106,818 | 1,598.8032 | 1,664 | 0.0156 | 0.9858 | 53,062.3156 |
| 0.5 | 178,029 | 1,611.7231 | 1,684 | 0.0095 | 0.9976 | -16,856.6875 |

## Caveats

- Propensity is not uplift: the model ranks who is likely to adopt, including customers who would adopt without contact. Measuring the incremental effect of a campaign needs a randomised targeting experiment.
- Associations in the odds ratios and importances are correlational, not causal.
- Trained on a customer sample and 2015 to 2016 Spanish bank data; performance can drift over time.
