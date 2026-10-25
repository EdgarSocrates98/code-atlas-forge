"""Fixture: Spark/Iceberg job — declared catalog, writeTo, MERGE, meta-table."""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

spark.conf.set(
    "spark.sql.catalog.lake", "org.apache.iceberg.spark.SparkCatalog"
)
spark.conf.set(
    "spark.sql.catalog.lake.warehouse", "s3://lake-wh/warehouse"
)

orders = spark.read.parquet("s3://inbound-bucket/orders/")

orders.writeTo("lake.db.orders").using("iceberg").append()

spark.sql(
    "MERGE INTO lake.db.orders t "
    "USING updates_src s ON t.id = s.id "
    "WHEN MATCHED THEN UPDATE SET t.qty = s.qty "
    "WHEN NOT MATCHED THEN INSERT (id, qty) VALUES (s.id, s.qty)"
)

spark.sql("INSERT INTO lake.db.orders_archive SELECT * FROM lake.db.orders")

# meta-tabela Iceberg endereçável estaticamente
spark.sql("SELECT snapshot_id FROM lake.db.orders.snapshots")
