# Data dictionary

Source: Kaggle competition "Santander Product Recommendation" (2016), file `train_ver2.csv`.
One row per customer per monthly snapshot. Meanings follow the competition's data description;
cleaning rules are implemented in `sql/staging/00_stg_holdings.sql`.

The Parquet cache (`data/cache/holdings/`) keeps the raw text of every attribute column. Only
`fecha_dato` (date), `ncodpers` (integer) and the 24 product flags (TINYINT) are typed during
conversion, so every cleaning decision is visible in SQL and covered by tests.

## Customer attributes: raw column to staging column

| Raw column | Staging column | Type | Meaning | Cleaning |
|---|---|---|---|---|
| `fecha_dato` | `snapshot_date` | DATE | Snapshot date (always the 28th) | none |
| (derived) | `month_index` | INTEGER | Months since the first configured snapshot (0 to 16) | `date_diff('month', months.first, fecha_dato)` |
| `ncodpers` | `customer_id` | BIGINT | Customer code | none |
| `ind_empleado` | `employee_index` | VARCHAR | Employee status: A active, B ex-employee, F filial, N not employee, P passive | trim, empty to NULL |
| `pais_residencia` | `country` | VARCHAR | Country of residence | trim, empty to NULL |
| `sexo` | `sex` | VARCHAR | Sex code (H or V); the source does not document the mapping, so codes are kept as-is | trim, empty to NULL |
| `age` | `age` | INTEGER | Age in years | padded text trimmed and cast; unparsable to NULL |
| `fecha_alta` | `join_date` | DATE | Date the customer first held a contract with the bank | cast; NULL kept (excluded from join cohorts) |
| `ind_nuevo` | `is_new_customer` | TINYINT | 1 if the customer registered in the last 6 months | trim and cast |
| `antiguedad` | `seniority_months` | INTEGER | Customer seniority in months | trim and cast; sentinel `-999999` to NULL |
| `indrel` | `primary_status` | TINYINT | 1 primary customer; 99 primary during the month but not at month end | trim and cast |
| `ult_fec_cli_1t` | `last_primary_date` | DATE | Last date as primary customer (when not primary at month end) | cast |
| `indrel_1mes` | `customer_type` | VARCHAR | Customer type at the start of the month: 1 primary, 2 co-owner, P potential, 3 former primary, 4 former co-owner | spellings such as `1.0` and `3.0` normalised to `1`..`4`, `P`; anything else to NULL |
| `tiprel_1mes` | `relation_type` | VARCHAR | Relation type at the start of the month: A active, I inactive, P former, R potential | trim, empty to NULL |
| `indresi` | `is_resident` | BOOLEAN | Residence country is the bank's country | `S` to true |
| `indext` | `is_foreign_born` | BOOLEAN | Birth country differs from the bank's country | `S` to true |
| `conyuemp` | `is_employee_spouse` | BOOLEAN | Customer is the spouse of an employee (mostly empty) | `S` to true; to be confirmed against the real file |
| `canal_entrada` | `channel` | VARCHAR | Channel the customer joined through | trim, empty to NULL |
| `indfall` | `is_deceased` | BOOLEAN | Deceased flag | `S` to true |
| `tipodom` | `address_type` | TINYINT | Address type (1 primary address) | trim and cast |
| `cod_prov` | `province_code` | INTEGER | Province code | cast |
| `nomprov` | `province_name` | VARCHAR | Province name (some contain commas and are quoted) | trim, empty to NULL |
| `ind_actividad_cliente` | `is_active` | TINYINT | 1 active customer, 0 inactive | trim and cast |
| `renta` | `income` | DOUBLE | Gross household income (EUR) | cast; text such as `NA` to NULL |
| (derived) | `income_missing` | BOOLEAN | Income is NULL after cleaning | `income IS NULL` |
| `segmento` | `segment` | VARCHAR | 01 VIP, 02 individuals, 03 university graduates | trim, empty to NULL |
| (derived) | `flags_filled_from_null` | TINYINT | Number of product flags that were NULL in this row and set to 0 | counted before filling |

## Product flags

The 24 flag columns keep their source names (for example `ind_tjcr_fin_ult1`) in staging and
in the fact table. A flag is 1 if the customer holds the product in that month. NULL flags
(only seen in `ind_nomina_ult1` and `ind_nom_pens_ult1`) become 0 and are counted in
`flags_filled_from_null` and in `reports/data_quality.md`. English names and families are in
`config/products.yaml` and `dim_product`.

## Warehouse tables

| Table | Grain | Columns |
|---|---|---|
| `stg_holdings` | customer x month | every column above plus the 24 flags |
| `product_events` | customer x month x product | `customer_id`, `month_index`, `product_code`, `event_type` (adoption or attrition); consecutive months only |
| `product_changes_across_gaps` | customer x month x product | flag changes where the previous observation is more than one month earlier; reported, never counted as events |
| `dim_customer` | customer | `customer_id`, `join_date`, `join_month`, `first_seen_month_index`, `last_seen_month_index`, `months_present`, `latest_segment`, `sex`, `age_at_first_seen`, `province`, `channel`, `income` |
| `dim_product` | product | `product_code`, `product_name`, `family`, `ladder_step` (NULL until the ladder is confirmed), `catalogue_order` |
| `dim_month` | snapshot month | `month_index`, `snapshot_date`, `month_label` (YYYY-MM), `customers` |
| `fact_monthly_holdings` | customer x month | `customer_id`, `month_index`, 24 flags (TINYINT), `n_products`, `is_active`, `seniority_months`, `segment`, `relation_type`, `is_new_customer` |

In `dim_customer`, "latest" attributes (segment, sex, province, channel, income) come from the
most recent month where the value is not NULL. `join_date` is the earliest one reported, and
`age_at_first_seen` comes from the first month that reports an age.
