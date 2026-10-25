"""Fixture: mesma tabela em contas distintas — identidades NÃO colidem."""

from awsglue.context import GlueContext
from pyspark.context import SparkContext

glueContext = GlueContext(SparkContext())

prod = glueContext.create_dynamic_frame.from_catalog(
    database="shared_db",
    table_name="dim_customer",
    catalog_id="111111111111",
)

dev = glueContext.create_dynamic_frame.from_catalog(
    database="shared_db",
    table_name="dim_customer",
    catalog_id="222222222222",
)

# mesma tabela sem catalog_id — escopo desconhecido, não deve fundir
local = glueContext.create_dynamic_frame.from_catalog(
    database="shared_db",
    table_name="dim_customer",
)
