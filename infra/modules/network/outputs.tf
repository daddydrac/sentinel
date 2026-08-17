output "vpc_id" { value = aws_vpc.this.id }
output "private_subnet_ids" { value = [for subnet in aws_subnet.private : subnet.id] }
output "emr_security_group_id" { value = aws_security_group.emr.id }
output "mcp_security_group_id" { value = aws_security_group.mcp.id }

