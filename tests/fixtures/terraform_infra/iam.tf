# IAM: roles, trust chain, policy e attachment.

resource "aws_iam_role" "glue" {
  name = "glue-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Cadeia de roles: a trust policy de lambda_exec nomeia glue como
# principal — glue pode assumir lambda_exec.
resource "aws_iam_role" "lambda_exec" {
  name = "lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.glue.arn }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "glue_s3" {
  name = "glue-s3"

  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = ["${aws_s3_bucket.data.arn}/*"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue" {
  role       = aws_iam_role.glue.name
  policy_arn = aws_iam_policy.glue_s3.arn
}

resource "aws_iam_role_policy" "extra" {
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.extra.json
}
