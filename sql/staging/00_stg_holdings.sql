-- Staging: one row per customer and snapshot month, typed and renamed to English.
-- Raw quirks handled here (details in docs/data_dictionary.md):
--   padded numbers are trimmed and cast; antiguedad -999999 becomes NULL;
--   indrel_1mes spellings '1', '1.0', 'P' ... are normalised to 1, 2, 3, 4, P;
--   renta text such as '   NA' becomes NULL and income_missing is set;
--   NULL product flags become 0 and are counted in flags_filled_from_null.
-- Product flags keep their source codes (ind_..._ult1); dim_product holds the English names.
-- Parameters: $holdings_glob (Parquet cache files), $first_month (first snapshot month, DATE).

CREATE OR REPLACE TABLE stg_holdings AS
SELECT
    fecha_dato AS snapshot_date,
    date_diff('month', $first_month::DATE, fecha_dato)::INTEGER AS month_index,
    ncodpers AS customer_id,
    nullif(trim(ind_empleado), '') AS employee_index,
    nullif(trim(pais_residencia), '') AS country,
    nullif(trim(sexo), '') AS sex,
    try_cast(trim(age) AS INTEGER) AS age,
    try_cast(trim(fecha_alta) AS DATE) AS join_date,
    try_cast(trim(ind_nuevo) AS TINYINT) AS is_new_customer,
    nullif(try_cast(trim(antiguedad) AS INTEGER), -999999) AS seniority_months,
    try_cast(trim(indrel) AS TINYINT) AS primary_status,
    try_cast(trim(ult_fec_cli_1t) AS DATE) AS last_primary_date,
    CASE
        WHEN regexp_replace(upper(trim(indrel_1mes)), '\.0$', '') IN ('1', '2', '3', '4', 'P')
            THEN regexp_replace(upper(trim(indrel_1mes)), '\.0$', '')
    END AS customer_type,
    nullif(trim(tiprel_1mes), '') AS relation_type,
    trim(indresi) = 'S' AS is_resident,
    trim(indext) = 'S' AS is_foreign_born,
    trim(conyuemp) = 'S' AS is_employee_spouse,
    nullif(trim(canal_entrada), '') AS channel,
    trim(indfall) = 'S' AS is_deceased,
    try_cast(trim(tipodom) AS TINYINT) AS address_type,
    try_cast(trim(cod_prov) AS INTEGER) AS province_code,
    nullif(trim(nomprov), '') AS province_name,
    try_cast(trim(ind_actividad_cliente) AS TINYINT) AS is_active,
    try_cast(trim(renta) AS DOUBLE) AS income,
    try_cast(trim(renta) AS DOUBLE) IS NULL AS income_missing,
    nullif(trim(segmento), '') AS segment,
    (len([*COLUMNS('^ind_.*_ult1$')]) - list_count([*COLUMNS('^ind_.*_ult1$')]))::TINYINT
        AS flags_filled_from_null,
    coalesce(COLUMNS('^ind_.*_ult1$'), 0)::TINYINT
FROM read_parquet($holdings_glob);
