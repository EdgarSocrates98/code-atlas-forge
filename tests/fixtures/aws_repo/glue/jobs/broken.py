"""Fixture: sintaxe quebrada — scan degradado por tokenize."""

import awsglue  # marker parcial

def oops(  # <- SyntaxError proposital
    path = "s3://rescue-bucket/still-visible/"
    role = "arn:aws:iam::999888777666:role/RescueRole"
