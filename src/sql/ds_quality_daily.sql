WITH workspaces AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, workspace_name, status
  FROM system.access.workspaces_latest
),
questions AS (SELECT * FROM __TARGET__.wt_base_questions),
agent_labels AS (SELECT * FROM __TARGET__.wt_base_agents),
feedback AS (SELECT * FROM __TARGET__.wt_base_feedback),
comments AS (SELECT * FROM __TARGET__.wt_base_comments),
genie_sql AS (SELECT * FROM __TARGET__.wt_base_genie_sql)
, s AS (SELECT event_date, agent_id,
               count_if(execution_status = 'FINISHED') AS sql_succeeded,
               count_if(execution_status = 'FAILED') AS sql_failed,
               count_if(execution_status = 'CANCELED') AS sql_canceled,
               count_if(execution_status = 'FINISHED' AND produced_rows = 0) AS empty_results
        FROM genie_sql GROUP BY 1, 2),
f AS (SELECT event_date, agent_id, sum(is_positive) AS thumbs_up, sum(is_negative) AS thumbs_down
      FROM feedback GROUP BY 1, 2),
c AS (SELECT event_date, agent_id, count(*) AS comments FROM comments GROUP BY 1, 2),
k AS (SELECT event_date, agent_id FROM s UNION SELECT event_date, agent_id FROM f
      UNION SELECT event_date, agent_id FROM c)
SELECT k.event_date, a.workspace_name, a.agent_label,
       coalesce(s.sql_succeeded, 0) AS sql_succeeded, coalesce(s.sql_failed, 0) AS sql_failed,
       coalesce(s.sql_canceled, 0) AS sql_canceled, coalesce(s.empty_results, 0) AS empty_results,
       coalesce(f.thumbs_up, 0) AS thumbs_up, coalesce(f.thumbs_down, 0) AS thumbs_down,
       coalesce(c.comments, 0) AS comments
FROM k JOIN agent_labels a ON a.agent_id = k.agent_id
LEFT JOIN s ON s.event_date = k.event_date AND s.agent_id = k.agent_id
LEFT JOIN f ON f.event_date = k.event_date AND f.agent_id = k.agent_id
LEFT JOIN c ON c.event_date = k.event_date AND c.agent_id = k.agent_id
