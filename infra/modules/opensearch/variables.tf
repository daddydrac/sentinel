variable "prefix" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "client_security_group_ids" { type = list(string) }
variable "kms_key_arn" { type = string }
variable "engine_version" { type = string }
variable "instance_type" { type = string }
variable "instance_count" { type = number }
variable "dedicated_master_type" { type = string }
variable "dedicated_master_count" { type = number }
variable "volume_size" { type = number }
variable "volume_iops" { type = number }
variable "volume_throughput" { type = number }
variable "enable_gpu_acceleration" { type = bool }
variable "log_retention_days" { type = number }

