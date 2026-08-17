output "api_arn" { value = aws_cloudformation_stack.this.outputs["ApiArn"] }
output "api_id" { value = aws_cloudformation_stack.this.outputs["ApiId"] }
output "http_endpoint" { value = aws_cloudformation_stack.this.outputs["HttpEndpoint"] }
output "realtime_endpoint" { value = aws_cloudformation_stack.this.outputs["RealtimeEndpoint"] }
output "channel_namespace" { value = "sessions" }
