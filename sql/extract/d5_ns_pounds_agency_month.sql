-- D5: pounds distributed per customer per month, NetSuite. Save as d5_ns_pounds_agency_month.csv
-- Output: agency_no, month, pounds, n_fulfillments
--
-- Source: item fulfillments created from sales orders, using the fulfillment's total order weight
-- (CUSTBODY_MHI_HFB_TOT_ORDER_WEIGHT). Checked by the Snowflake assistant on Sep 2026: 8,792 of 9,012
-- orders match their sales-order line weights within 1 lb; total within 0.3% of qty x item weight.
-- (CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY was rejected: a partial backfill created on/after 2026-10-12
-- with whole months missing.)
--
-- Rules:
--   * latest Stitch version of each record (by ID, _SDC_BATCHED_AT)
--   * sales-order fulfillments only (drops transfer orders, vendor returns); not voided
--   * dates compared as UTC calendar dates (TRANDATE is midnight UTC)
--   * CUTOVER: only dates from 2025-08-01; Ceres (D10) is used for the months before. On Aug 1-8 2025
--     both systems ran and NetSuite already holds 94% of Ceres's pounds for the same agency and day
--     (d5_d10_overlap_check.sql). src/data/cycle3_make_dataset.py enforces the same cutover.
--   * complete months only (before the 1st of the current month); drops the 2035-dated order
--   * DELETEDRECORD can't be applied (no NetSuite IDs in it); a known, unmeasured gap
--   * every customer is included (agencies, mobile pantries, schools, individuals' account, food banks,
--     disaster accounts). No names: the pipeline only needs the number.

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
lines AS (
    SELECT UPPER(TRIM(c.ENTITYID))                              AS agency_no,
           CONVERT_TIMEZONE('UTC', f.TRANDATE)::DATE            AS tran_date,
           f.CUSTBODY_MHI_HFB_TOT_ORDER_WEIGHT                  AS lbs
    FROM f
    LEFT JOIN c ON c.ID = f.ENTITY
    WHERE f.ORDERTYPE = 'SalesOrd'
      AND COALESCE(f.VOIDED, 'F') = 'F'
)
SELECT agency_no,
       DATE_TRUNC('month', tran_date) AS month,
       SUM(lbs)                       AS pounds,
       COUNT(*)                       AS n_fulfillments
FROM lines
WHERE tran_date >= '2025-08-01'
  AND tran_date <  DATE_TRUNC('month', CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE)
GROUP BY 1, 2
ORDER BY 1, 2;

-- Sense-check before downloading:
-- WITH d5 AS (<query above>)
-- SELECT COUNT(*) n_rows, COUNT(DISTINCT agency_no) n_customers, MIN(month), MAX(month),   -- 2025-08 .. last complete month
--        SUM(pounds) total_lbs,
--        SUM(IFF(agency_no IS NULL, pounds, 0)) lbs_no_customer,                           -- expect 0
--        COUNT(*) - COUNT(DISTINCT agency_no || '|' || month) dup_keys                     -- expect 0
-- FROM d5;
