"""Fixture: boto3 Athena query + Glue create_job com Role ARN literal."""

import boto3

athena = boto3.client("athena")
glue = boto3.client("glue")

athena.start_query_execution(
    QueryString=(
        "INSERT INTO reporting.daily_totals "
        "SELECT day, SUM(total) FROM lake_db.agg_orders GROUP BY day"
    ),
    QueryExecutionContext={
        "Database": "lake_db",
        "Catalog": "AwsDataCatalog",
    },
    ResultConfiguration={
        "OutputLocation": "s3://athena-results-bucket/daily/"
    },
)

glue.create_job(
    Name="nightly-enrich",
    Role="arn:aws:iam::123456789012:role/GlueNightlyRole",
    Command={
        "Name": "glueetl",
        "ScriptLocation": "s3://glue-scripts-bucket/nightly_enrich.py",
    },
    DefaultArguments={
        "--output_uri": "s3://enriched-bucket/out/",
        "--secrets_key": "hunter2",
    },
)
