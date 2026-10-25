# Módulo local → PROVISIONS; módulo registry → REFERENCES external.

module "network" {
  source      = "./modules/network"
  environment = var.environment
  vpc_cidr    = "10.0.0.0/16"
}

module "shared" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
}
