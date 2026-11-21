-- D7: customers whose volume isn't local client service. Save as d7_exclusions.csv
-- Column names confirmed by discovery on 2026-11-21.
--
-- Rules:
--   disaster            ENTITYID starts DISA- or category DISASTER
--   transfer_food_bank  ENTITYID starts FBFL- / FBKY- or category FOODBANK
--   hub_candidate       has sales orders whose EXTERNALID contains -HUB- (e.g. "...-POLK0060-OB-HUB-TEFAP"),
--                       or name contains hub / redistribution
--   internal_candidate  category Internal (FTB's own accounts)
-- Review the *_candidate rows before uploading (FTB decision #7): delete rows that are ordinary local
-- service, add known hubs with flag = hub. Every row left in the file is excluded from "local service".
-- Other categories (Shopper, SCHOOLS, SFSP, GRANTS, ...) stay in; the pipeline reports pounds by
-- category so FTB can decide later.

WITH c AS (
    SELECT *
    FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
cat AS (
    SELECT ID, NAME
    FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMERCATEGORY
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
hub_orders AS (
    SELECT ENTITY AS customer_id, COUNT(*) AS n_hub_orders
    FROM STITCH.SNOWFLAKE_NS_INT.SALESORDER
    WHERE EXTERNALID ILIKE '%-HUB-%'
    GROUP BY 1
),
flagged AS (
    SELECT
        UPPER(TRIM(c.ENTITYID))  AS agency_no,
        c.COMPANYNAME            AS agency_name,
        cat.NAME                 AS category,
        COALESCE(h.n_hub_orders, 0) AS n_hub_orders,
        CASE
            WHEN c.ENTITYID ILIKE 'DISA-%' OR cat.NAME = 'DISASTER'                   THEN 'disaster'
            WHEN c.ENTITYID ILIKE ANY ('FBFL-%', 'FBKY-%') OR cat.NAME = 'FOODBANK'   THEN 'transfer_food_bank'
            WHEN h.n_hub_orders > 0
              OR c.COMPANYNAME ILIKE ANY ('%hub%', '%redistrib%')                     THEN 'hub_candidate'
            WHEN cat.NAME = 'Internal'                                                THEN 'internal_candidate'
        END AS flag
    FROM c
    LEFT JOIN cat ON cat.ID = c.CATEGORY
    LEFT JOIN hub_orders h ON h.customer_id = c.ID
    WHERE COALESCE(c.ISPERSON, 'F') = 'F'
)
SELECT * FROM flagged WHERE flag IS NOT NULL ORDER BY flag, agency_no;

-- Sense-check: FY25-26 pounds and share by flag (and by category for the rest)
-- WITH d7 AS (<query above>), d5 AS (<D5 main query>)
-- SELECT COALESCE(d7.flag, 'local') flag, COUNT(DISTINCT d5.agency_no) n_agencies, SUM(d5.pounds) lbs,
--        RATIO_TO_REPORT(SUM(d5.pounds)) OVER () share
-- FROM d5 LEFT JOIN d7 USING (agency_no)
-- WHERE d5.month BETWEEN '2025-08-01' AND '2026-06-01'
-- GROUP BY 1 ORDER BY lbs DESC;
