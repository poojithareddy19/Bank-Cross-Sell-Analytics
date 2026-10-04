-- One row per product, from config/products.yaml (registered by warehouse.py as
-- products_config). ladder_step and ladder_step_name come from the confirmed ladder in
-- products.yaml; they are NULL for products not on the ladder (or while it is empty).

CREATE OR REPLACE TABLE dim_product AS
SELECT
    product_code,
    product_name,
    family,
    ladder_step::INTEGER AS ladder_step,
    ladder_step_name::VARCHAR AS ladder_step_name,
    catalogue_order::INTEGER AS catalogue_order
FROM products_config;
