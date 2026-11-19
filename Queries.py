# Queries.py — the "useful queries" deliverable, in plain SQL over data/drtracker.db
import pandas as pd
from schema import connect

pd.set_option("display.width", 120)

# Q1: How many times did each patient vomit each week?
# SUM skips NULL frequencies, so "vomited, didn't say how often" stays unknown instead of
# becoming 0. Denials ("no vomiting today", severity 0) are excluded.
Q1 = """
SELECT patient_id,
       strftime('%Y-W%W', onset)  AS week,
       SUM(frequency)             AS vomiting_episodes,
       COUNT(*)                   AS mentions
FROM events
WHERE symptom = 'vomiting'
  AND (severity IS NULL OR severity > 0)
GROUP BY patient_id, week
ORDER BY patient_id, week;
"""

# Q2: Did nausea change after a medication change?
# Mean nausea severity in the 7 days before vs the 7 days after each new anti-nausea drug
# started mid-treatment (picked via medications.reason, so the mouth rinse isn't included).
Q2 = """
SELECT m.patient_id,
       m.drug,
       m.start_date,
       ROUND(AVG(CASE WHEN julianday(e.onset) <  julianday(m.start_date)
                       AND julianday(e.onset) >= julianday(m.start_date) - 7
                      THEN e.severity END), 1) AS nausea_7d_before,
       ROUND(AVG(CASE WHEN julianday(e.onset) >= julianday(m.start_date)
                       AND julianday(e.onset) <  julianday(m.start_date) + 7
                      THEN e.severity END), 1) AS nausea_7d_after,
       SUM(CASE WHEN julianday(e.onset) BETWEEN julianday(m.start_date) - 7
                                            AND julianday(m.start_date) + 6
                 AND e.severity IS NOT NULL THEN 1 ELSE 0 END) AS n_scored_reports
FROM medications m
JOIN events e ON e.patient_id = m.patient_id AND e.symptom = 'nausea'
JOIN patients p ON p.patient_id = m.patient_id
WHERE m.reason LIKE '%nausea%'
  AND julianday(m.start_date) > julianday(p.treatment_start)
GROUP BY m.patient_id, m.drug, m.start_date
ORDER BY m.patient_id;
"""

# Q3: How much is still waiting for a human? Review queue per patient, and why.
Q3 = """
SELECT patient_id,
       SUM(needs_review)                                         AS needs_review,
       COUNT(*)                                                  AS events,
       ROUND(100.0 * SUM(needs_review) / COUNT(*), 0)            AS pct_review,
       SUM(flags LIKE '%same_day_conflict%')                     AS same_day_conflicts,
       SUM(extracted_by = 'rule')                                AS via_regex_fallback
FROM events
GROUP BY patient_id
ORDER BY pct_review DESC;
"""

def run(title, sql, conn):
    print(f"\n=== {title}")
    print(pd.read_sql(sql, conn).to_string(index=False))

if __name__ == "__main__":
    with connect() as conn:
        run("Q1  Vomiting episodes per patient per week", Q1, conn)
        run("Q2  Nausea 7 days before vs after a medication change", Q2, conn)
        run("Q3  Review queue per patient", Q3, conn)
