-- Cross-domain eval workspace (lf-0124): DDL que outros adapters
-- observam. staging.orders/staging.events têm contrapartida PySpark
-- com identidade qualificada idêntica (resolução cross-domain plena);
-- shared.metrics existe em DOIS catálogos — o read PySpark sem
-- qualificador de catálogo é ambiguidade honesta, nunca resolvida.

CREATE TABLE staging.orders (
    order_id     BIGINT,
    customer_id  BIGINT,
    amount       DECIMAL(10,2)
)
STORED AS PARQUET;

CREATE TABLE staging.events (
    event_id BIGINT,
    payload  STRING
)
STORED AS PARQUET;

CREATE TABLE cat_a.shared.metrics (
    metric_key STRING,
    metric_val DOUBLE
);

CREATE TABLE cat_b.shared.metrics (
    metric_key STRING,
    metric_val DOUBLE
);
