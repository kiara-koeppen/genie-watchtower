WITH curation AS (
  SELECT a.event_date, a.request_params['space_id'] AS agent_id, lower(a.user_identity.email) AS user_email,
         CASE WHEN a.action_name IN ('createEvaluationRun', 'genieCreateEvalRun') THEN 1 ELSE 0 END AS is_benchmark
  FROM system.access.audit a
  WHERE a.service_name = 'aibiGenie'
    AND a.action_name IN ('createInstruction', 'updateInstruction', 'deleteInstruction', 'createCuratedQuestion', 'updateCuratedQuestion', 'deleteCuratedQuestion', 'updateGenieColumnConfigs', 'addDataSources', 'removeDataSources', 'updateSpace', 'genieUpdateSpace', 'updateSampleQuestions', 'createEvaluationRun', 'genieCreateEvalRun')
    AND a.response.status_code = 200
    AND a.request_params['space_id'] IS NOT NULL
)
SELECT * FROM curation
