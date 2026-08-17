output "application_id" {
  value = aws_emrserverless_application.this.id
}

output "job_role_arn" {
  value = aws_iam_role.job.arn
}

output "generate_script_uri" {
  value = "s3://${var.bucket_name}/${aws_s3_object.generate.key}"
}

output "graphrag_script_uri" {
  value = "s3://${var.bucket_name}/${aws_s3_object.graphrag.key}"
}

output "acquisition_project_name" {
  value = aws_codebuild_project.acquisition.name
}

output "acquisition_project_arn" {
  value = aws_codebuild_project.acquisition.arn
}

output "source_prefix" {
  value = "s3://${var.bucket_name}/hpc/raw/source/${var.dataset_revision}/"
}
