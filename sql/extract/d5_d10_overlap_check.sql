-- Overlap check, Ceres (BC) vs NetSuite, daily, 2025-04-01 .. 2025-08-31.
-- Save as d5_d10_overlap_check.csv (about 150 rows, totals only, no agency detail).
-- Tells us whether NetSuite's pre-cutover fulfillments (Apr-Aug 8 2025) are the same shipments as in
-- Ceres (double entry, safe to drop) or extra shipments Ceres doesn't have.

WITH f AS (
    SELECT *
    FROM STITCH.SNOWFLAKE_NS_INT.ITEMFULFILLMENT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
c AS (
    SELECT ID, ENTITYID
    FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
ns AS (
    SELECT CONVERT_TIMEZONE('UTC', f.TRANDATE)::DATE AS day,
           UPPER(TRIM(c.ENTITYID))                   AS agency_no,
           SUM(f.CUSTBODY_MHI_HFB_TOT_ORDER_WEIGHT)  AS lbs
    FROM f LEFT JOIN c ON c.ID = f.ENTITY
    WHERE f.ORDERTYPE = 'SalesOrd' AND COALESCE(f.VOIDED, 'F') = 'F'
      AND CONVERT_TIMEZONE('UTC', f.TRANDATE)::DATE BETWEEN '2025-04-01' AND '2025-08-31'
    GROUP BY 1, 2
),
bc AS (
    SELECT TO_TIMESTAMP("Posting Date", 6)::DATE      AS day,
           UPPER(TRIM("Sell-to Customer No_"))        AS agency_no,
           SUM("Ext_ Gross Weight")                   AS lbs
    FROM FTB.BRONZE."Feeding Tampa Bay_Sales Shipment Line"
    WHERE "Type" = 2
      AND TO_TIMESTAMP("Posting Date", 6)::DATE BETWEEN '2025-04-01' AND '2025-08-31'
    GROUP BY 1, 2
)
SELECT COALESCE(ns.day, bc.day)                         AS day,
       SUM(bc.lbs)                                      AS bc_lbs,
       SUM(ns.lbs)                                      AS ns_lbs,
       COUNT(bc.agency_no)                              AS bc_agencies,
       COUNT(ns.agency_no)                              AS ns_agencies,
       COUNT(IFF(bc.agency_no IS NOT NULL AND ns.agency_no IS NOT NULL, 1, NULL)) AS agencies_in_both,
       SUM(IFF(bc.agency_no IS NOT NULL AND ns.agency_no IS NOT NULL, ns.lbs, 0)) AS ns_lbs_same_agency_day
FROM ns
FULL OUTER JOIN bc ON bc.day = ns.day AND bc.agency_no = ns.agency_no
GROUP BY 1
ORDER BY 1;
