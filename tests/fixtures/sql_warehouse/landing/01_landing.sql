-- Landing zone: ingestão crua dos sistemas de origem.
-- Cada CREATE declara a tabela física; os INSERTs registram a chegada
-- dos dados (fonte externa não visível — apenas DDL + carga literal).

CREATE TABLE landing.orders (
    order_id     BIGINT,
    customer_id  BIGINT,
    amount       DECIMAL(10,2),
    currency     STRING,
    created_at   TIMESTAMP
)
COMMENT 'raw orders snapshot'
STORED AS PARQUET;

CREATE TABLE landing.customers (
    customer_id  BIGINT,
    region       STRING,
    segment      STRING
)
STORED AS PARQUET;

-- carga diária de reconciliação manual (VALUES — sem upstream)
INSERT INTO landing.customers (customer_id, region, segment)
VALUES (0, 'unknown', 'none');
