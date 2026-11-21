-- D10: Ceres (old Business Central ERP) pounds per customer per month, 2018-01 .. 2025-08-08.
-- Save as d10_ceres_pounds_agency_month.csv
-- Output: agency_no, month, pounds, n_lines
--
-- Columns confirmed by the Snowflake assistant on 2026-11-21:
--   * FTB.BRONZE."Feeding Tampa Bay_Sales Shipment Line": item lines have "Type" = 2;
--     pounds per line = "Ext_ Gross Weight" (= Quantity x Gross Weight on every line checked);
--     customer = "Sell-to Customer No_", same codes as NetSuite CUSTOMER.ENTITYID
--   * "Posting Date" is microseconds since 1970 -> TO_TIMESTAMP(x, 6)::DATE; 1753-01-01 means blank
--   * shipment lines and posted invoice lines give identical pounds every year; Item Ledger is 1-2% lower
--     (probably netted returns). We use shipment lines = what went out the door.
--   * last posting date 2025-08-08. The pipeline uses Ceres for months before 2025-08 only (cutover).
-- Yearly totals from discovery, to check against: 2018 52.4M, 2019 69.5M, 2020 94.4M, 2021 104.4M,
-- 2022 92.6M, 2023 91.0M, 2024 101.5M, 2025 (to Aug 8) 60.0M.

SELECT
    UPPER(TRIM("Sell-to Customer No_"))                       AS agency_no,
    DATE_TRUNC('month', TO_TIMESTAMP("Posting Date", 6)::DATE) AS month,
    SUM("Ext_ Gross Weight")                                   AS pounds,
    COUNT(*)                                                   AS n_lines
FROM FTB.BRONZE."Feeding Tampa Bay_Sales Shipment Line"
WHERE "Type" = 2
  AND TO_TIMESTAMP("Posting Date", 6)::DATE BETWEEN '2018-01-01' AND '2025-08-08'
GROUP BY 1, 2
ORDER BY 1, 2;

-- Sense-check before downloading:
-- WITH d10 AS (<query above>)
-- SELECT YEAR(month) yr, COUNT(*) n_rows, COUNT(DISTINCT agency_no) n_customers, SUM(pounds) lbs
-- FROM d10 GROUP BY 1 ORDER BY 1;                    -- lbs should match the yearly totals above
-- WITH d10 AS (<query above>)
-- SELECT COUNT(*) - COUNT(DISTINCT agency_no || '|' || month) dup_keys FROM d10;   -- expect 0

-- D10b: location of Ceres customers (for agencies that closed before NetSuite and so aren't in D6).
-- Save as d10b_ceres_customer_location.csv. No names: the pipeline only needs number, ZIP and county.
SELECT UPPER(TRIM("No_"))       AS agency_no,
       LEFT(TRIM("Post Code"), 5) AS ceres_zip,
       "FBC County Code"        AS ceres_county_code,
       "FBC Agency Category Code" AS ceres_agency_category   -- to tell agencies from schools etc. before 2025-08
FROM FTB.BRONZE."Feeding Tampa Bay_Customer"
ORDER BY 1;

-- D10c: street address of Ceres customers, for geocoding (src/data/cycle3_geocode.py).
-- Save as d10c_ceres_customer_addresses.csv. Output: agency_no, address, city, zip
-- (BC standard columns "Address" / "City" / "Post Code"; check the names if the query errors.)
SELECT UPPER(TRIM("No_"))          AS agency_no,
       TRIM("Address")             AS address,
       TRIM("City")                AS city,
       LEFT(TRIM("Post Code"), 5)  AS zip
FROM FTB.BRONZE."Feeding Tampa Bay_Customer"
WHERE NULLIF(TRIM("Address"), '') IS NOT NULL
ORDER BY 1;
