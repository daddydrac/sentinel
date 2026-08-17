output "app_id" { value = aws_amplify_app.this.id }
output "branch_name" { value = aws_amplify_branch.this.branch_name }
output "default_domain" { value = aws_amplify_app.this.default_domain }
output "url" { value = "https://${aws_amplify_branch.this.branch_name}.${aws_amplify_app.this.default_domain}" }
output "arn" { value = aws_amplify_app.this.arn }
