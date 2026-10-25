-- Staging: enriquecimento + deduplicação sobre a landing zone.

CREATE OR REPLACE VIEW staging.v_enriched_orders AS
SELECT
    o.order_id,
    o.customer_id,
    o.amount AS amount_usd,
    c.region,
    c.segment
FROM landing.orders o
JOIN landing.customers c ON c.customer_id = o.customer_id;

-- order_facts: fatos deduplicados — CTE encadeia sobre a view.
INSERT INTO staging.order_facts
    (order_id, customer_id, amount_usd, region)
WITH ranked AS (
    SELECT
        order_id,
        customer_id,
        amount_usd,
        region,
        ROW_NUMBER() OVER (
            PARTITION BY order_id ORDER BY order_id
        ) AS rn
    FROM staging.v_enriched_orders
)
SELECT order_id, customer_id, amount_usd, region
FROM ranked
WHERE rn = 1;
