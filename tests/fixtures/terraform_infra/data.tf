# Data sources: leituras locais + remote state (external).

data "aws_iam_policy_document" "extra" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
}

data "terraform_remote_state" "network" {
  backend = "s3"
  config = {
    bucket = "acme-tfstate"
    key    = "network/terraform.tfstate"
  }
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "pipeline"
  role_arn = aws_iam_role.glue.arn

  definition = jsonencode({
    States = {
      Run = { Resource = aws_lambda_function.trigger.arn }
    }
  })
}
