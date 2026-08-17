output "demo_url" {
  value = aws_apigatewayv2_stage.this.invoke_url
}

output "api_id" {
  value = aws_apigatewayv2_api.this.id
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "function_arn" {
  value = aws_lambda_function.this.arn
}
