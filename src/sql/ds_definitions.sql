SELECT * FROM VALUES
  ('Active users', 'Distinct people who asked at least one Genie question in the last 30 complete days.'),
  ('Questions', 'Messages sent to a Genie agent (UI, API, MCP and agent mode), successful requests only.'),
  ('Active agents', 'Genie agents (formerly Genie spaces) that received at least one question in the window.'),
  ('Stickiness (DAU/MAU)', 'Average daily active users divided by 30-day active users. Higher means habitual use.'),
  ('User retention', 'Share of last period''s active users who came back this period.'),
  ('SQL success rate', 'Genie-generated SQL statements that finished, out of finished plus failed (system.query.history).'),
  ('Empty result rate', 'Finished Genie SQL statements that returned zero rows. A spike can mean the agent misunderstands filters.'),
  ('Positive feedback rate', 'Positive ratings out of positive plus negative ratings (latest rating per message).'),
  ('Genie billable cost', 'Genie pay-as-you-go usage at list price (billing_origin_product = GENIE, excluding free usage). Corrections (RETRACTION/RESTATEMENT rows) net out automatically.'),
  ('Free usage value', 'Genie usage billed to the free SKU, valued at the list price of the account''s billable Genie SKU. Shows what this usage would cost once free usage ends.'),
  ('SQL compute cost (est.)', 'Each warehouse''s daily list-price cost, attributed to Genie agents by their share of that warehouse''s query time.'),
  ('Total cost', 'Genie billable cost plus estimated SQL compute cost. List prices only: negotiated discounts are not reflected.'),
  ('Projected cost after free period', 'Total cost plus free usage value for the same 30 days.'),
  ('Agent health', 'Trashed, Unused (never asked), Stale (no questions in 30 days), Needs attention (10+ questions and SQL success under 90% or positive feedback under 60%), Declining (questions down more than half), Growing, Healthy.'),
  ('User segment', 'New (first question in last 30 days), Power (8+ active days or 50+ questions), Regular (3+ days), Casual, Dormant (no questions in 30 days).'),
  ('Agent names', 'From the genie_agent_dim lookup table when present, otherwise the latest name seen in the audit log. Unnamed agents show the last 6 characters of their ID.')
AS t(kpi, definition)
