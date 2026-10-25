"""Arquivo malformado de propósito — ``ast.parse`` deve falhar.

O adapter registra ``parse-error`` (error) e segue: nunca levanta,
nunca emite entidade para conteúdo que não conseguiu analisar.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate(

df = spark.read.table("staging.orders"
df.write.saveAsTable("mart.out"
