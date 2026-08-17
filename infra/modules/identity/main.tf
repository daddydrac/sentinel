data "aws_region" "current" {}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.prefix}-operators"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "INACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 1
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }
}

resource "aws_cognito_user_pool_client" "ui" {
  name         = "${var.prefix}-ui"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret               = false
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  access_token_validity         = 15
  id_token_validity             = 15
  refresh_token_validity        = 1
  supported_identity_providers  = ["COGNITO"]

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_group" "investigator" {
  name         = "investigator"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Read-only HDFS GraphRAG investigators"
  precedence   = 30
}

resource "aws_cognito_user_group" "approver" {
  name         = "approver"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Operators allowed to approve exact read-only tool plans"
  precedence   = 20
}

resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Demo administrators"
  precedence   = 10
}
