#!/usr/bin/env bash
# Fixture: spark-submit literal — nunca executado pelo adapter.
spark-submit \
  --master yarn \
  --deploy-mode cluster \
  --conf spark.sql.catalog.lake=org.apache.iceberg.spark.SparkCatalog \
  s3://jobs-bucket/scripts/ingest.py

echo "done"
