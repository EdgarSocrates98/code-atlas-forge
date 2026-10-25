"""Job PySpark do workspace cross-domain (lf-0124).

``staging.orders``/``staging.events`` casam por identidade qualificada
com o DDL do adapter sql (FULL → resolved). ``shared.metrics`` não diz
o catálogo: ``cat_a.shared.metrics`` e ``cat_b.shared.metrics`` são
ambas PARTIAL — o resolver declara ambiguous com os dois candidatos.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

orders = spark.read.table("staging.orders")
events = spark.read.table("staging.events")
metrics = spark.read.table("shared.metrics")

joined = orders.join(events, orders.order_id == events.event_id)
joined.write.mode("overwrite").saveAsTable("mart.joined")

metrics.write.mode("append").saveAsTable("mart.metrics_out")
