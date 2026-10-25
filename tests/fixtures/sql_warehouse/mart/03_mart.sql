-- Mart: agregados de negócio sobre os fatos de staging.

CREATE TABLE mart.daily_revenue AS
SELECT
    region,
    COUNT(DISTINCT order_id) AS orders,
    SUM(amount_usd)          AS revenue
FROM staging.order_facts
GROUP BY region;

-- upsert incremental: customer_stats deriva dos fatos.
MERGE INTO mart.customer_stats t
USING (
    SELECT
        customer_id,
        SUM(amount_usd) AS total_spend,
        COUNT(*)        AS orders
    FROM staging.order_facts
    GROUP BY customer_id
) s
ON t.customer_id = s.customer_id
WHEN MATCHED THEN UPDATE SET
    total_spend = s.total_spend,
    orders      = s.orders
WHEN NOT MATCHED THEN INSERT (customer_id, total_spend, orders)
    VALUES (s.customer_id, s.total_spend, s.orders);
