"""ETL fixture — staging read → join → dedup → mart write (lf-0112).

Pipeline PySpark canônico: lê tabela de catálogo ``staging.orders``,
paths parquet/delta de clientes, encadeia
join→filter→dropDuplicates→withColumnRenamed e persiste com
``saveAsTable`` + ``write.parquet``.
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = (
    SparkSession.builder.appName("orders-etl")
    .config("spark.sql.shuffle.partitions", "8")
    .getOrCreate()
)

orders = spark.read.table("staging.orders")
customers = spark.read.parquet("s3://datalake/customers/")
archive = spark.read.format("delta").load("s3://datalake/customers_archive/")

all_customers = customers.union(archive)

enriched = (
    orders.join(
        all_customers,
        orders.customer_id == all_customers.id,
        "left",
    )
    .filter(F.col("status") == "paid")
    .dropDuplicates(["order_id"])
    .withColumnRenamed("amount", "total_amount")
)

enriched.write.mode("overwrite").saveAsTable("mart.enriched_orders")
enriched.write.parquet("s3://datalake/mart/enriched_orders/")
