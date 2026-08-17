output "repository_url" { value = aws_ecr_repository.this.repository_url }
output "repository_name" { value = aws_ecr_repository.this.name }
output "codebuild_project_name" { value = aws_codebuild_project.this.name }
output "endpoint_name" { value = try(aws_sagemaker_endpoint.this[0].name, null) }
output "endpoint_arn" { value = try(aws_sagemaker_endpoint.this[0].arn, null) }
output "model_id" { value = var.model_id }
output "model_revision" { value = var.model_revision }
