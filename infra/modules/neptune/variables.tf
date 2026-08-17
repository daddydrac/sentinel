variable "prefix" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "client_security_group_ids" { type = list(string) }
variable "bucket_arn" { type = string }
variable "kms_key_arn" { type = string }
variable "engine_version" { type = string }
variable "instance_class" { type = string }
variable "instance_count" { type = number }
variable "serverless_min_capacity" { type = number }
variable "serverless_max_capacity" { type = number }

