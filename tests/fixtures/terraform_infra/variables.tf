variable "environment" {
  type    = string
  default = "dev"
}

# Adversarial: defaults sensíveis NUNCA podem vazar para entidades,
# atributos, evidência ou diagnósticos.
variable "db_password" {
  type      = string
  sensitive = true
  default   = "sup3r-s3cret-do-not-leak"
}

variable "api_token" {
  default = "tok-live-adversarial"
}
