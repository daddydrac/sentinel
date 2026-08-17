variable "prefix" {
  type        = string
  description = "Resource-name prefix."
}

variable "force_destroy" {
  type        = bool
  description = "Allow deletion of non-empty demo evidence buckets."
}

variable "point_in_time_recovery" {
  type        = bool
  description = "Enable DynamoDB point-in-time recovery."
}
