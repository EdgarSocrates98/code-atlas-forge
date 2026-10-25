"""Job centrado em ``spark.sql`` — literal delegado ao adapter sql.

Cobre: ``spark.sql(SELECT … JOIN …)`` atribuído a var, ``INSERT INTO``
soltado no fluxo e ``df.writeTo`` (Iceberg-style, writerV2).
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

region_counts = spark.sql(
    "SELECT c.region, COUNT(*) AS n "
    "FROM staging.customers c "
    "JOIN staging.regions r ON c.region_id = r.id "
    "GROUP BY c.region"
)

spark.sql(
    "INSERT INTO mart.daily SELECT * FROM staging.daily_source"
)

region_counts.writeTo("mart.customer_region_counts").using(
    "iceberg"
).createOrReplace()
