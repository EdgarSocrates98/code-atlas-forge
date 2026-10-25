"""Fixture: Glue ETL job — DynamicFrame catalog read/write + SQL literal."""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session

job = Job(glueContext)
args = getResolvedOptions(
    sys.argv, ["JOB_NAME", "JOB_BOOKMARK_OPTION"]
)
job.init(
    args["JOB_NAME"],
    glueContext,
    args={"--JOB_BOOKMARK_OPTION": "job-bookmark-enable"},
)

orders_dyf = glueContext.create_dynamic_frame.from_catalog(
    database="sales_db",
    table_name="orders",
)

# leitura via options com paths s3 literais
raw_logs = glueContext.create_dynamic_frame.from_options(
    connection_type="s3",
    connection_options={"paths": ["s3://raw-logs-bucket/orders/daily/"]},
    format="json",
)

orders_df = orders_dyf.toDF()
orders_df.createOrReplaceTempView("orders_src")

spark.sql(
    "INSERT INTO lake_db.agg_orders "
    "SELECT order_id, total_usd FROM orders_src"
)

glueContext.write_dynamic_frame.from_catalog(
    frame=orders_dyf,
    database="lake_db",
    table_name="agg_orders",
)

job.commit()
