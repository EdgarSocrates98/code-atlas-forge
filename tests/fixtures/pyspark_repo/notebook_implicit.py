"""Cenário notebook/sessão implícita — ``spark`` existe sem declaração.

Em notebooks e ``spark-submit`` com sessão injetada, ``spark`` aparece
livre no módulo. O adapter trata ``spark.<método-de-sessão>`` como
sessão implícita — mesmo sem import ou builder no arquivo.
"""

df = spark.read.table("staging.events")

clean = (
    df.filter("is_valid = true")
    .select("id", "payload")
    .withColumnRenamed("payload", "body")
)

clean.write.insertInto("staging.events_clean")
