-- Step 0: discovery. Confirms real table/column names before the D5–D7 extracts.
-- Role CAPONETEAM. Run each statement separately in Snowsight.
-- Results stay local (data/raw/, gitignored); sample rows contain agency names.

-- 0.1 Which tables exist (save as discovery_ns_tables.csv)
SELECT table_name, row_count, last_altered
FROM STITCH.INFORMATION_SCHEMA.TABLES
WHERE table_schema = 'SNOWFLAKE_NS_INT'
  AND (   table_name ILIKE '%FOOD_DISTRIBUTION%'
       OR table_name ILIKE 'ITEMFULFILLMENT%'
       OR table_name ILIKE 'SALESORDER%'
       OR table_name ILIKE 'TRANSACTION%'
       OR table_name ILIKE '%ITEM'
       OR table_name ILIKE 'CUSTOMER%'
       OR table_name = 'DELETEDRECORD')
ORDER BY table_name;

-- 0.2 Columns of those tables (save as discovery_ns_columns.csv)
SELECT table_name, ordinal_position, column_name, data_type
FROM STITCH.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema = 'SNOWFLAKE_NS_INT'
  AND table_name IN ('CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY', 'ITEMFULFILLMENT',
                     'SALESORDER', 'SALESORDERITEM', 'ITEM', 'CUSTOMER', 'DELETEDRECORD')
ORDER BY table_name, ordinal_position;

-- 0.3 Three sample rows per key table (view only, no need to download; paste a screenshot or the
--     interesting column names/values into chat if easier)
SELECT * FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMRECORD_FOOD_DISTRIBUTION_SUMMARY LIMIT 3;
SELECT * FROM STITCH.SNOWFLAKE_NS_INT.ITEMFULFILLMENT LIMIT 3;
SELECT * FROM STITCH.SNOWFLAKE_NS_INT.ITEM LIMIT 3;
SELECT * FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER LIMIT 3;
SELECT * FROM STITCH.SNOWFLAKE_NS_INT.DELETEDRECORD LIMIT 3;

-- 0.4 FTB.GOLD.AGENCIES columns (save as discovery_gold_agencies_columns.csv)
SELECT ordinal_position, column_name, data_type
FROM FTB.INFORMATION_SCHEMA.COLUMNS
WHERE table_schema = 'GOLD' AND table_name = 'AGENCIES'
ORDER BY ordinal_position;

-- 0.5 Where do DISA- / FBFL- / FBKY- codes live? Customer numbers or item codes?
--     ENTITYID / ITEMID are GUESSES: replace with the real number columns from 0.2.
SELECT 'customer' AS tbl, SPLIT_PART(ENTITYID, '-', 1) AS prefix, COUNT(*) AS n
FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER
WHERE ENTITYID ILIKE ANY ('DISA-%', 'FBFL-%', 'FBKY-%')
GROUP BY 1, 2
UNION ALL
SELECT 'item', SPLIT_PART(ITEMID, '-', 1), COUNT(*)
FROM STITCH.SNOWFLAKE_NS_INT.ITEM
WHERE ITEMID ILIKE ANY ('DISA-%', 'FBFL-%', 'FBKY-%')
GROUP BY 1, 2;

-- 0.6 Customer categories (to spot hub / redistribution agencies). CATEGORY is a GUESS.
SELECT COALESCE(CATEGORY:name::STRING, CATEGORY::STRING) AS category, COUNT(*) AS n_customers
FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER
GROUP BY 1 ORDER BY 2 DESC;
