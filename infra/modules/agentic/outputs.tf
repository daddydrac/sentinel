output "agent_id" {
  value = one(aws_bedrockagent_agent.this[*].agent_id)
}

output "agent_arn" {
  value = one(aws_bedrockagent_agent.this[*].agent_arn)
}

output "agent_alias_id" {
  value = one(aws_bedrockagent_agent_alias.this[*].agent_alias_id)
}

output "agent_alias_arn" {
  value = one(aws_bedrockagent_agent_alias.this[*].agent_alias_arn)
}

output "graphrag_agent_id" {
  value = one(aws_bedrockagent_agent.graphrag[*].agent_id)
}

output "graphrag_agent_arn" {
  value = one(aws_bedrockagent_agent.graphrag[*].agent_arn)
}

output "graphrag_agent_alias_id" {
  value = one(aws_bedrockagent_agent_alias.graphrag[*].agent_alias_id)
}

output "graphrag_agent_alias_arn" {
  value = one(aws_bedrockagent_agent_alias.graphrag[*].agent_alias_arn)
}

output "gateway_url" {
  value = aws_bedrockagentcore_gateway.this.gateway_url
}

output "gateway_arn" {
  value = aws_bedrockagentcore_gateway.this.gateway_arn
}

output "gateway_id" {
  value = aws_bedrockagentcore_gateway.this.gateway_id
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.this.version
}

output "guardrail_arn" {
  value = aws_bedrock_guardrail.this.guardrail_arn
}

output "mcp_tool_function_name" {
  description = "Legacy remediation-tool Lambda."
  value       = aws_lambda_function.mcp_tool.function_name
}

output "graphrag_tool_function_name" {
  description = "Read-only GraphRAG Lambda used by the post-build smoke gate."
  value       = aws_lambda_function.graphrag_tool.function_name
}

output "graphrag_tool_function_arn" {
  value = aws_lambda_function.graphrag_tool.arn
}
