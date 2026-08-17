output "kms_key_arn" {
  value = aws_kms_key.this.arn
}

output "evidence_bucket_name" {
  value = aws_s3_bucket.evidence.id
}

output "evidence_bucket_arn" {
  value = aws_s3_bucket.evidence.arn
}

output "workflow_table_name" {
  value = aws_dynamodb_table.workflows.name
}

output "workflow_table_arn" {
  value = aws_dynamodb_table.workflows.arn
}
