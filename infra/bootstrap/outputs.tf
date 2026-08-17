output "bucket" {
  value = aws_s3_bucket.state.id
}

output "kms_key_arn" {
  value = aws_kms_key.state.arn
}

output "region" {
  value = var.aws_region
}
