-- Monthly spend trend for the most recent pipeline run.
SELECT
    month,
    invoice_count,
    spend
FROM vw_monthly_spend
ORDER BY month;
