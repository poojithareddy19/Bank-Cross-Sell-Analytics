-- Pair 1, baseline: adoption and attrition counts per product with a SELF-JOIN.
-- Each customer-month row is joined to the same customer's row one month earlier, which
-- builds a hash table over the whole fact table and writes out all 24 differences by hand.
-- Output must equal 01_events_window.sql.

WITH deltas AS (
    SELECT
        a.ind_ahor_fin_ult1 - b.ind_ahor_fin_ult1 AS ind_ahor_fin_ult1,
        a.ind_aval_fin_ult1 - b.ind_aval_fin_ult1 AS ind_aval_fin_ult1,
        a.ind_cco_fin_ult1 - b.ind_cco_fin_ult1 AS ind_cco_fin_ult1,
        a.ind_cder_fin_ult1 - b.ind_cder_fin_ult1 AS ind_cder_fin_ult1,
        a.ind_cno_fin_ult1 - b.ind_cno_fin_ult1 AS ind_cno_fin_ult1,
        a.ind_ctju_fin_ult1 - b.ind_ctju_fin_ult1 AS ind_ctju_fin_ult1,
        a.ind_ctma_fin_ult1 - b.ind_ctma_fin_ult1 AS ind_ctma_fin_ult1,
        a.ind_ctop_fin_ult1 - b.ind_ctop_fin_ult1 AS ind_ctop_fin_ult1,
        a.ind_ctpp_fin_ult1 - b.ind_ctpp_fin_ult1 AS ind_ctpp_fin_ult1,
        a.ind_deco_fin_ult1 - b.ind_deco_fin_ult1 AS ind_deco_fin_ult1,
        a.ind_deme_fin_ult1 - b.ind_deme_fin_ult1 AS ind_deme_fin_ult1,
        a.ind_dela_fin_ult1 - b.ind_dela_fin_ult1 AS ind_dela_fin_ult1,
        a.ind_ecue_fin_ult1 - b.ind_ecue_fin_ult1 AS ind_ecue_fin_ult1,
        a.ind_fond_fin_ult1 - b.ind_fond_fin_ult1 AS ind_fond_fin_ult1,
        a.ind_hip_fin_ult1 - b.ind_hip_fin_ult1 AS ind_hip_fin_ult1,
        a.ind_plan_fin_ult1 - b.ind_plan_fin_ult1 AS ind_plan_fin_ult1,
        a.ind_pres_fin_ult1 - b.ind_pres_fin_ult1 AS ind_pres_fin_ult1,
        a.ind_reca_fin_ult1 - b.ind_reca_fin_ult1 AS ind_reca_fin_ult1,
        a.ind_tjcr_fin_ult1 - b.ind_tjcr_fin_ult1 AS ind_tjcr_fin_ult1,
        a.ind_valo_fin_ult1 - b.ind_valo_fin_ult1 AS ind_valo_fin_ult1,
        a.ind_viv_fin_ult1 - b.ind_viv_fin_ult1 AS ind_viv_fin_ult1,
        a.ind_nomina_ult1 - b.ind_nomina_ult1 AS ind_nomina_ult1,
        a.ind_nom_pens_ult1 - b.ind_nom_pens_ult1 AS ind_nom_pens_ult1,
        a.ind_recibo_ult1 - b.ind_recibo_ult1 AS ind_recibo_ult1
    FROM fact_monthly_holdings AS a
    JOIN fact_monthly_holdings AS b
      ON b.customer_id = a.customer_id AND b.month_index = a.month_index - 1
),
changed AS (
    SELECT * FROM deltas
    WHERE list_min([*COLUMNS(*)]) < 0 OR list_max([*COLUMNS(*)]) > 0
)
SELECT
    product_code,
    count(*) FILTER (WHERE delta = 1) AS adoptions,
    count(*) FILTER (WHERE delta = -1) AS attritions
FROM (UNPIVOT changed ON COLUMNS(*) INTO NAME product_code VALUE delta)
WHERE delta <> 0
GROUP BY product_code
ORDER BY product_code;
