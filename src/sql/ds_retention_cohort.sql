WITH questions AS (SELECT * FROM __TARGET__.wt_base_questions)
, firsts AS (SELECT user_email, trunc(min(event_date), 'MM') AS cohort_month FROM questions GROUP BY 1),
activity AS (SELECT DISTINCT user_email, trunc(event_date, 'MM') AS active_month FROM questions),
sizes AS (SELECT cohort_month, count(*) AS cohort_size FROM firsts GROUP BY 1)
SELECT f.cohort_month,
       CAST(months_between(a.active_month, f.cohort_month) AS INT) AS months_since_first,
       concat('Month ', CAST(months_between(a.active_month, f.cohort_month) AS INT)) AS month_label,
       s.cohort_size,
       count(DISTINCT a.user_email) AS retained_users,
       round(count(DISTINCT a.user_email) / s.cohort_size, 3) AS retention_rate
FROM firsts f JOIN activity a ON a.user_email = f.user_email
JOIN sizes s ON s.cohort_month = f.cohort_month
GROUP BY ALL
