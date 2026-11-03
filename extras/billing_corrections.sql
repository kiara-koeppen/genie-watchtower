-- Genie / AI billing corrections: link every RETRACTION to the ORIGINAL row it reverses,
-- and to the RESTATEMENT that replaces it (if any).
-- A correction keeps every field of the original except usage_quantity (docs:
-- https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/billing#record-type),
-- so rows are paired on all non-quantity fields plus the absolute quantity.
WITH u AS (
  SELECT *,
    sha2(concat_ws('|', account_id, workspace_id, sku_name, cloud, usage_unit, billing_origin_product,
         cast(usage_start_time AS STRING), cast(usage_end_time AS STRING), cast(usage_date AS STRING),
         to_json(usage_metadata), to_json(identity_metadata), to_json(custom_tags), to_json(product_features)), 256) AS correction_key
  FROM system.billing.usage
  WHERE usage_date >= :start_date
),
orig AS (
  SELECT *, row_number() OVER (PARTITION BY correction_key, abs(usage_quantity) ORDER BY ingestion_date, record_id) AS rn
  FROM u WHERE record_type = 'ORIGINAL'
),
retr AS (
  SELECT *, row_number() OVER (PARTITION BY correction_key, abs(usage_quantity) ORDER BY ingestion_date, record_id) AS rn
  FROM u WHERE record_type = 'RETRACTION'
),
rest AS (
  SELECT correction_key, collect_list(record_id) AS restatement_record_ids,
         sum(usage_quantity) AS restated_quantity, max(ingestion_date) AS restatement_ingestion_date
  FROM u WHERE record_type = 'RESTATEMENT' GROUP BY correction_key
)
SELECT
  r.workspace_id, r.billing_origin_product, r.sku_name, r.usage_date,
  r.record_id                       AS retraction_record_id,
  o.record_id                       AS original_record_id,
  rest.restatement_record_ids,
  o.usage_quantity                  AS original_quantity,
  r.usage_quantity                  AS retraction_quantity,
  o.ingestion_date                  AS original_ingestion_date,
  r.ingestion_date                  AS retraction_ingestion_date,
  datediff(r.ingestion_date, r.usage_date) AS days_until_corrected,
  CASE WHEN o.record_id IS NULL THEN 'Unmatched: original outside the date window'
       WHEN rest.correction_key IS NULL THEN 'Removed: usage reversed with no replacement'
       ELSE 'Restated: usage replaced with corrected values' END AS correction_type
FROM retr r
LEFT JOIN orig o ON o.correction_key = r.correction_key AND abs(o.usage_quantity) = abs(r.usage_quantity) AND o.rn = r.rn
LEFT JOIN rest   ON rest.correction_key = r.correction_key
