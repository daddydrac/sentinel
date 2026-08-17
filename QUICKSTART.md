# AWS HDFS GraphRAG — Quick Start

The default profile creates high-capacity OpenSearch, Neptune, EMR Serverless, SageMaker, and related services. Use a dedicated disposable AWS account, review `infra/environments/demo.tfvars`, and confirm quotas before deployment.

## 1. Authenticate and set safety gates

Required tools: Terraform 1.10+, AWS CLI v2, Python 3.11+, Node/npm, `make`, `curl`, `jq`, and `zip`.

```bash
aws sso login --profile YOUR_PROFILE
export AWS_PROFILE=YOUR_PROFILE
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
export INITIAL_OPERATOR_EMAIL=primary.admin@example.com
export INITIAL_OPERATOR_GROUPS=admin
export CONFIRM_HPC_COST=100G-GRAPHRAG

aws sts get-caller-identity
```

Do not put credentials or user files in source control or `.tfvars`.

## 2. Validate and deploy everything

```bash
make test
make preflight
make deploy
```

`make deploy` performs both Terraform/model phases, invites the initial Cognito operator, starts the managed acquisition/generation/GraphRAG state machine, requires its publication and top-three smoke gates to pass, and then publishes the UI. For unattended apply in the verified sandbox, use `AUTO_APPROVE=1 make deploy`.

Get the application URLs:

```bash
terraform -chdir=infra output -raw graphrag_ui_url
terraform -chdir=infra output -raw open_demo_command
```

The first command is the Cognito-authenticated GraphRAG experience. The second is the preserved legacy remediation experience and contains a sensitive disposable token.

## 3. Add users and assign roles

New users receive Cognito invitations. Existing users are reconciled to exactly the requested managed groups.

```bash
make add-user EMAIL=oncall.approver@example.com GROUPS=approver
make add-user EMAIL=platform.admin@example.com GROUPS=admin
```

Bulk provisioning:

```bash
cp config/users.example.csv /secure/path/users.csv
# Edit the protected copy.
make add-users FILE=/secure/path/users.csv
```

`approver` and `admin` can accept/reject exact MCP plans. `investigator` has owner-bound chat access but cannot make approval decisions. To provision the bulk file during `make deploy`, export `DEPLOY_USERS_FILE=/secure/path/users.csv` first.

## 4. Demonstrate GraphRAG chat

Sign in through the Amplify URL and ask:

> Identify the three most important anomalous HDFS log behaviors and give me an action plan.

Bedrock Agent returns its selected read-only calls to the chat. Review the exact tools, arguments, reason, expiry, and plan hash. Select **Approve exact plan**. No selected AgentCore MCP call occurs before approval. If the Agent requests another batch, approve its new hash separately.

The completed view must show exactly three findings, graph/vector evidence, citations, and three human actions per finding. The left panel is a public tool/authority trace, not hidden model reasoning.

## 5. Re-run the qualification

```bash
export CONFIRM_HPC_COST=100G-GRAPHRAG
make benchmark-100g
```

The output must include `query_ready_smoke_passed: true` and `slo_passed: true`. The latter measures the staged-data Spark GraphRAG build, not Terraform, public acquisition, corpus generation, or model startup. Preserve three successful manifests before claiming the ten-minute objective as reproducible.

## 6. Destroy the entire stack

```bash
export CONFIRM_DESTROY=hpe-agentic-remediation-demo
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
make destroy-all
```

Use `AUTO_APPROVE=1 make destroy-all` only in the confirmed disposable account. The script fails if Terraform still tracks application resources or tagged non-KMS resources remain. KMS keys enter the AWS-required pending-deletion window.
