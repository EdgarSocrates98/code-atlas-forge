"""Job com SQL e paths dinâmicos — honestidade, nunca fabricação.

Todos os alvos abaixo são dinâmicos: f-string, parâmetros de função e
lookup em config. O adapter deve registrar ``dynamic-sql`` /
``dynamic-source`` / ``dynamic-write-target`` + arestas
``to_unresolved`` — e NENHUMA tabela fabricada.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()


def run(table_name: str, query: str, out_path: str) -> None:
    live = spark.sql(f"SELECT * FROM {table_name}")
    templated = spark.sql(query)
    source = spark.read.parquet(out_path)
    merged = live.union(templated).union(source)
    merged.write.mode("append").saveAsTable(f"mart.{table_name}")
    merged.write.parquet(out_path)


def resolve(name):
    return spark.table(name)


def unknown_receiver() -> None:
    src = resolve("staging.incoming")
    src.filter("keep = 1").write.insertInto("staging.incoming_clean")
