For this full demo deployment, the simplest reliable option is to give the deployment principal the AWS-managed `AdministratorAccess` policy:

```text
arn:aws:iam::aws:policy/AdministratorAccess
```

That policy grants `Action: "*" / Resource: "*"`, which covers the complete Terraform deployment. [AWS AdministratorAccess reference](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AdministratorAccess.html)

### Recommended approach

Use an IAM Identity Center permission set or assumed deployment role with `AdministratorAccess`, rather than a long-lived IAM user/access key. AWS recommends temporary role credentials for human users. [AWS IAM security best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)

Configure the CLI through SSO:

```bash
aws configure sso
aws sso login --profile graphrag-admin

export AWS_PROFILE=graphrag-admin
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1

aws sts get-caller-identity
```

Then set the repository’s deployment gates:

```bash
export EXPECTED_AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export EXPECTED_AWS_REGION="us-east-1"
export CONFIRM_HPC_COST="100G-GRAPHRAG"
export INITIAL_OPERATOR_EMAIL="your-email@example.com"
export INITIAL_OPERATOR_GROUPS="admin"
```

Run:

```bash
make preflight
make deploy
```

### If you must use an IAM user

Attach the managed policy:

```bash
aws iam attach-user-policy \
  --user-name graphrag-deployer \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

Require MFA and avoid permanent access keys where possible.

### Why full administrator permission is needed

Terraform creates and connects resources across:

* IAM roles, policies, service roles, and `iam:PassRole`
* Bedrock, Bedrock Agents, Guardrails, and AgentCore
* OpenSearch Service
* Neptune and `neptune-db`
* EMR Serverless
* SageMaker
* CodeBuild and ECR
* Lambda and Step Functions
* S3, KMS, and DynamoDB
* VPC, subnets, endpoints, and security groups
* API Gateway, AppSync, Amplify, Cognito, and CloudFormation
* CloudWatch Logs, WAF, Budgets, and resource tagging

`iam:PassRole` is particularly important because Terraform creates execution roles and passes them to Lambda, EMR Serverless, SageMaker, Step Functions, Bedrock, CodeBuild, Neptune, and CloudFormation. [AWS `iam:PassRole` guidance](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_passrole.html)

### AdministratorAccess can still be blocked

Deployment may fail even with `AdministratorAccess` if the account has:

* An AWS Organizations SCP denying a required service
* A permissions boundary attached to the deployment principal
* A restrictive session policy
* Insufficient service quotas, especially SageMaker `ml.g5.2xlarge`, OpenSearch nodes/storage, Neptune capacity, EMR Serverless vCPUs, or Lambda concurrency
* Missing Amazon Nova model access in `us-east-1`
* Region restrictions
* KMS, IAM, Bedrock, or Marketplace explicit denies

Confirm Bedrock access to:

```text
amazon.nova-lite-v1:0
amazon.nova-2-multimodal-embeddings-v1:0
```

The repository’s `make preflight` checks visibility and API access, but you should also confirm Nova access in the Bedrock console. [Amazon Bedrock model-access guidance](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)

Finally, the Cognito `investigator`, `approver`, and `admin` users are application users. They do **not** need AWS IAM permissions; only the Terraform deployment operator needs the AWS administrator role.
