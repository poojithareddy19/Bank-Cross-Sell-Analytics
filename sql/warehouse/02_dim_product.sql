-- One row per product, from config/products.yaml (registered by warehouse.py as
-- products_config). ladder_step stays NULL until the ladder is frozen in Milestone 4.

CREATE OR REPLACE TABLE dim_product AS
SELECT
    product_code,
    product_name,
    family,
    ladder_step::INTEGER AS ladder_step,
    catalogue_order::INTEGER AS catalogue_order
FROM products_config;
