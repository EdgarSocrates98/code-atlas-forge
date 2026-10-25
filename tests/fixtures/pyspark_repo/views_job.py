"""Temp views — ``createOrReplaceTempView`` + leituras pelo alias.

``orders_v`` sombreia o catálogo dentro deste arquivo: leituras pelo
nome registram aresta na view, e a view carrega ``DERIVES_FROM`` para
a fonte física — ``mart.joined`` colapsa nas fontes terminais.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

orders = spark.read.table("staging.orders")
orders.createOrReplaceTempView("orders_v")

joined = spark.sql(
    "SELECT o.id, c.segment "
    "FROM orders_v o "
    "JOIN staging.customers c ON o.customer_id = c.id"
)

joined.write.mode("overwrite").saveAsTable("mart.joined")
