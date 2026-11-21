-- D6: agency -> location. Save as d6_agency_location.csv
-- Column names confirmed by discovery on 2026-11-21.
--
-- One row per customer that received food (appears in the distribution summary or in item fulfillments)
-- or is listed in FTB.GOLD.AGENCIES. Individuals (ISPERSON = 'T') get no name or address.
--
-- Location sources, in the order the pipeline uses them:
--   1. gold_tract_geoid  FTB.GOLD.AGENCIES.GEOID_AGENCES (291 agencies)
--   2. gold_zip          FTB.GOLD.AGENCIES.ZIP_CODE
--   3. ship_zip          ZIP parsed from the ship-to address of the agency's latest fulfillment
--                        (CUSTOMER has no address text; DEFAULTSHIPPINGADDRESS is only an ID)
-- GOLD stores ZIP and GEOID as FLOAT, so they are cast via NUMBER to text (no ".0", no 1.2E10).

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
f AS (
    SELECT *
    FROM STITCH.SNOWFLAKE_NS_INT.ITEMFULFILLMENT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
last_ship AS (   -- latest non-voided fulfillment per customer, with a 5-digit ZIP in its ship address
    SELECT ENTITY AS customer_id,
           -- address text ends "... Hudson FL 34674"; Florida ZIPs start 32-34
           REGEXP_SUBSTR(SHIPADDRESS, '(FL|Florida),?[[:space:]]+(3[234][0-9]{3})', 1, 1, 'ie', 2) AS ship_zip,
           TRANDATE::DATE AS ship_zip_date
    FROM f
    WHERE COALESCE(VOIDED, 'F') = 'F'
      AND REGEXP_SUBSTR(SHIPADDRESS, '(FL|Florida),?[[:space:]]+(3[234][0-9]{3})', 1, 1, 'ie', 2) IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ENTITY ORDER BY TRANDATE DESC, ID DESC) = 1
),
active AS (      -- customers that received anything
    SELECT DISTINCT CUSTRECORD_FDS_AGENCY AS customer_id
    FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY
    UNION
    SELECT DISTINCT ENTITY FROM f
),
g AS (
    SELECT UPPER(TRIM(AGENCY_NO))                      AS agency_no,
           LPAD(TO_VARCHAR(ZIP_CODE::NUMBER(5, 0)), 5, '0')          AS gold_zip,
           TO_VARCHAR(GEOID_AGENCES::NUMBER(11, 0))    AS gold_tract_geoid,
           COUNTY_SHEET2                               AS gold_county
    FROM FTB.GOLD.AGENCIES
    QUALIFY ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(AGENCY_NO)) ORDER BY GEOID_AGENCES NULLS LAST) = 1
),
ns AS (
    SELECT c.ID                                         AS customer_id,
           UPPER(TRIM(c.ENTITYID))                      AS agency_no,
           IFF(c.ISPERSON = 'T', NULL, c.COMPANYNAME)   AS agency_name,
           cat.NAME                                     AS category,
           c.CUSTENTITY_FB_MHI_UNC_COUNTY               AS ns_county_code,   -- list ID (FBC_COUNTY points to a non-FL list)
           c.ENTITYSTATUS                               AS status_code,
           c.ISINACTIVE                                 AS is_inactive,
           c.ISPERSON                                   AS is_person,
           IFF(c.ISPERSON = 'T', NULL, ls.ship_zip)     AS ship_zip,
           ls.ship_zip_date
    FROM c
    JOIN active a ON a.customer_id = c.ID
    LEFT JOIN cat ON cat.ID = c.CATEGORY
    LEFT JOIN last_ship ls ON ls.customer_id = c.ID
)
SELECT
    COALESCE(ns.agency_no, g.agency_no) AS agency_no,
    ns.agency_name, ns.category, ns.ns_county_code, ns.status_code, ns.is_inactive, ns.is_person,
    ns.ship_zip, ns.ship_zip_date,
    g.gold_zip, g.gold_tract_geoid, g.gold_county,
    (ns.agency_no IS NOT NULL) AS in_netsuite,
    (g.agency_no  IS NOT NULL) AS in_gold
FROM ns
FULL OUTER JOIN g ON g.agency_no = ns.agency_no
ORDER BY 1;

-- Sense-check before downloading:
-- WITH d6 AS (<query above>)
-- SELECT COUNT(*) n_rows, COUNT(DISTINCT agency_no) n_agencies,              -- expect equal
--        SUM(IFF(in_netsuite AND in_gold, 1, 0)) n_both,                       -- expect ~291
--        SUM(IFF(gold_tract_geoid IS NOT NULL, 1, 0)) n_with_tract,
--        SUM(IFF(gold_tract_geoid IS NOT NULL AND LENGTH(gold_tract_geoid) <> 11, 1, 0)) n_bad_geoid,  -- expect 0
--        SUM(IFF(gold_tract_geoid IS NULL AND COALESCE(gold_zip, ship_zip) IS NULL, 1, 0)) n_no_location
-- FROM d6;
-- Also look at the most common ship_zip values: if one ZIP has dozens of agencies it's probably an FTB
-- warehouse (pickup orders), not the agency's own address. Tell Claude which ZIP(s).
-- WITH d6 AS (<query above>) SELECT ship_zip, COUNT(*) n FROM d6 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
