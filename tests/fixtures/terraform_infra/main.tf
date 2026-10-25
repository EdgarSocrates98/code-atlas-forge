# Fixture: infra pequena mas realista — recursos managed com deps
# implícitas e explícitas. NUNCA executável: o adapter apenas lê.

resource "aws_s3_bucket" "data" {
  bucket = "acme-data-${var.environment}"

  tags = {
    Environment = var.environment
  }
}

resource "aws_glue_job" "etl" {
  name     = "etl-${var.environment}"
  role_arn = aws_iam_role.glue.arn

  command {
    script_location = "s3://${aws_s3_bucket.data.bucket}/scripts/etl.py"
  }

  depends_on = [aws_iam_role_policy_attachment.glue]
}

resource "aws_lambda_function" "trigger" {
  function_name = "trigger"
  role          = aws_iam_role.lambda_exec.arn
  s3_bucket     = aws_s3_bucket.data.id
  runtime       = "python3.12"
  handler       = "index.handler"
}

resource "aws_cloudwatch_log_group" "lambda" {
  name = "/aws/lambda/${aws_lambda_function.trigger.function_name}"
}

# Fora do catálogo fechado — vira TerraformResource genérico ("other").
resource "random_id" "suffix" {
  byte_length = 4
}
