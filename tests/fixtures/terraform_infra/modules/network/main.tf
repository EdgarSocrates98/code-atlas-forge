resource "aws_s3_bucket" "flow_logs" {
  bucket = "flow-logs-${var.environment}"
}

resource "aws_cloudwatch_log_group" "flow" {
  name = "/vpc/flow/${aws_s3_bucket.flow_logs.id}"
}
