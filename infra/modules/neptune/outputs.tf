output "cluster_arn" { value = aws_neptune_cluster.this.arn }
output "database_resource_arn" { value = local.database_resource_arn }
output "writer_endpoint" { value = aws_neptune_cluster.this.endpoint }
output "reader_endpoint" { value = aws_neptune_cluster.this.reader_endpoint }
output "port" { value = aws_neptune_cluster.this.port }
output "loader_role_arn" { value = aws_iam_role.loader.arn }
output "security_group_id" { value = aws_security_group.this.id }

