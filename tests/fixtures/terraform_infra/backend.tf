terraform {
  backend "s3" {
    bucket = "acme-tfstate"
    key    = "infra/terraform.tfstate"
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
