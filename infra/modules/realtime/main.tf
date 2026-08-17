resource "aws_cloudformation_stack" "this" {
  name = "${var.prefix}-events"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "AppSync Events streaming plane for the HDFS GraphRAG demo"
    Resources = {
      EventApi = {
        Type = "AWS::AppSync::Api"
        Properties = {
          Name = "${var.prefix}-events"
          EventConfig = {
            AuthProviders = [
              {
                AuthType = "AMAZON_COGNITO_USER_POOLS"
                CognitoConfig = {
                  AwsRegion        = var.aws_region
                  UserPoolId       = var.user_pool_id
                  AppIdClientRegex = "^${var.user_pool_client_id}$"
                }
              },
              { AuthType = "AWS_IAM" }
            ]
            ConnectionAuthModes       = [{ AuthType = "AMAZON_COGNITO_USER_POOLS" }]
            DefaultPublishAuthModes   = [{ AuthType = "AWS_IAM" }]
            DefaultSubscribeAuthModes = [{ AuthType = "AMAZON_COGNITO_USER_POOLS" }]
          }
        }
      }
      Namespace = {
        Type = "AWS::AppSync::ChannelNamespace"
        Properties = {
          ApiId              = { "Fn::GetAtt" = ["EventApi", "ApiId"] }
          Name               = "sessions"
          PublishAuthModes   = [{ AuthType = "AWS_IAM" }]
          SubscribeAuthModes = [{ AuthType = "AMAZON_COGNITO_USER_POOLS" }]
          # APPSYNC_JS has no RegExp: a regular-expression literal fails to compile
          # with "Error processing APPSYNC_JS code". util.matches is the supported
          # equivalent. Verified by creating each variant as a real namespace.
          CodeHandlers = <<-JS
            import { util } from '@aws-appsync/utils'

            export function onSubscribe(ctx) {
              const requested = ctx.info.channel.path
              const allowedPrefix = '/sessions/' + ctx.identity.sub + '/'
              const chatId = requested.slice(allowedPrefix.length)
              if (!requested.startsWith(allowedPrefix) || !util.matches('^chat-[a-f0-9-]{36}$', chatId)) {
                util.unauthorized()
              }
            }
          JS
          # HandlerConfigs is for DIRECT data-source integrations and requires an
          # Integration block. Code handlers are declared by CodeHandlers alone;
          # supplying Behavior = "CODE" without Integration fails template
          # validation before any resource is created.
        }
      }
    }
    Outputs = {
      ApiArn = { Value = { "Fn::GetAtt" = ["EventApi", "ApiArn"] } }
      ApiId  = { Value = { "Fn::GetAtt" = ["EventApi", "ApiId"] } }
      HttpEndpoint = {
        Value = { "Fn::Join" = ["", ["https://", { "Fn::GetAtt" = ["EventApi", "Dns.Http"] }, "/event"]] }
      }
      RealtimeEndpoint = {
        Value = { "Fn::Join" = ["", ["wss://", { "Fn::GetAtt" = ["EventApi", "Dns.Realtime"] }, "/event/realtime"]] }
      }
    }
  })
}
