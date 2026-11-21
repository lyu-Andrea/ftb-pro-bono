-- D6b: street address per NetSuite customer, for geocoding to census tracts (src/data/cycle3_geocode.py).
-- Save as d6b_customer_addresses.csv. Output: agency_no, address, city, zip
--
-- CUSTOMER has no address text (DEFAULTSHIPPINGADDRESS is only an ID). The address comes from the ship-to
-- text of the customer's latest non-voided fulfillment, e.g. "Contact\nPantry name\n7009 Hudson Ave\nHudson FL 34674\nUS".
-- Only the street line (the line before "City FL 3xxxx") and the city/ZIP are exported; contact names stay
-- in Snowflake. Individuals (ISPERSON = 'T') are excluded.

WITH f AS (
    SELECT *
    FROM STITCH.SNOWFLAKE_NS_INT.ITEMFULFILLMENT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
c AS (
    SELECT ID, ENTITYID, ISPERSON
    FROM STITCH.SNOWFLAKE_NS_INT.CUSTOMER
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ID ORDER BY _SDC_BATCHED_AT DESC) = 1
),
latest AS (
    SELECT f.ENTITY AS customer_id,
           REPLACE(REPLACE(f.SHIPADDRESS, '&lt;br&gt;', '\n'), '\r', '') AS txt
    FROM f
    WHERE COALESCE(f.VOIDED, 'F') = 'F' AND f.SHIPADDRESS IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY f.ENTITY ORDER BY f.TRANDATE DESC, f.ID DESC) = 1
),
lines AS (
    SELECT l.customer_id, s.index AS i, TRIM(s.value::STRING) AS line
    FROM latest l, LATERAL FLATTEN(input => SPLIT(l.txt, '\n')) s
),
zip_line AS (
    SELECT customer_id, i, line
    FROM lines
    WHERE REGEXP_LIKE(line, '.*(FL|Florida),?[[:space:]]+3[234][0-9]{3}.*', 'i')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY i) = 1
)
SELECT
    UPPER(TRIM(c.ENTITYID))                                                    AS agency_no,
    street.line                                                                AS address,
    TRIM(REGEXP_REPLACE(z.line, ',?[[:space:]]+(FL|Florida).*$', '', 1, 0, 'i')) AS city,
    REGEXP_SUBSTR(z.line, '3[234][0-9]{3}')                                    AS zip
FROM zip_line z
JOIN lines street ON street.customer_id = z.customer_id AND street.i = z.i - 1
JOIN c ON c.ID = z.customer_id
WHERE COALESCE(c.ISPERSON, 'F') = 'F'
ORDER BY 1;

-- Sense-check: rows ~ number of customers with fulfillments (about 500); address looks like a street
-- ("7009 Hudson Ave"), not a company name. A few will show a name instead; the geocoder just won't match those.
-- WITH d AS (<query above>) SELECT COUNT(*), COUNT(DISTINCT agency_no), SUM(IFF(REGEXP_LIKE(address, '^[0-9].*'), 1, 0)) starts_with_number FROM d;
