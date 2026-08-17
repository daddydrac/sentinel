# Two-Day Deployment and Demo Runbook

## Goal

Deploy, qualify, rehearse, and tear down the complete Cognito-authenticated HDFS GraphRAG and preserved remediation demo in one dedicated AWS sandbox.

## Day 1 — Validate and deploy

### 08:00–10:00 — Account, quotas, and source review

- Confirm a disposable account, Region, budget authority, and tags.
- Review `infra/environments/demo.tfvars` and the cost-sized OpenSearch, Neptune, EMR, and SageMaker profile.
- Confirm Bedrock Agent, AgentCore, Nova embeddings, OpenSearch 3.1/GPU acceleration, Neptune, EMR Serverless, AppSync Events, Amplify, Cognito, WAF, and SageMaker access/quotas.
- Review the engineering, implementation, reconciliation, and teardown documents.

```bash
export AWS_PROFILE=YOUR_PROFILE
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
aws sts get-caller-identity
make test
make preflight
```

### 10:00–12:00 — Terraform review

```bash
terraform -chdir=infra init -backend=false
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra validate
terraform -chdir=infra plan -var-file=environments/demo.tfvars
```

Stop for an unrelated-resource plan, wrong account/Region, inaccessible model, missing quota, or any unbounded Agent tool.

### 13:00–17:00 — Complete deployment

```bash
export INITIAL_OPERATOR_EMAIL=primary.admin@example.com
export INITIAL_OPERATOR_GROUPS=admin
export CONFIRM_HPC_COST=100G-GRAPHRAG
make deploy
```

This provisions the stack/model, invites the operator, performs managed acquisition and 100 GiB GraphRAG loading, validates the stores and live top-three tool, then publishes the UI. Retain the Step Functions execution and S3 manifest.

Provision the separate on-call approver:

```bash
make add-user EMAIL=oncall.approver@example.com GROUPS=approver
```

Validate sign-in/new-password flow, admin and approver decisions, investigator denial, owner isolation, AppSync streaming, and the preserved remediation URL.

## Day 2 — Qualify and rehearse

### 08:00–11:00 — Three-run performance evidence

```bash
export CONFIRM_HPC_COST=100G-GRAPHRAG
make benchmark-100g
make benchmark-100g
make benchmark-100g
```

Retain each run ID, execution ARN, manifest URI, physical bytes, staged-data seconds, counts, and smoke result. If any run lacks `slo_passed=true`, present 600 seconds as an unachieved target and investigate metrics without weakening gates.

### 11:00–13:00 — Security and failure proofs

- Intervene with configured Guardrail cases.
- Reject a tool plan and prove zero selected MCP execution.
- Test an expired/changed hash and expect conflict.
- Sign in as investigator and expect HTTP 403 on a tool decision.
- Refresh group claims after role changes.
- Confirm labels are absent from online mappings and graph artifacts.
- Run the original remediation verified and compensation scenarios.

### 13:00–15:00 — Audience rehearsal

- Follow `docs/DEMO_SCRIPT.md`.
- Show the public 25% trace, exact in-chat approval, top-three results, citations, and human action plan.
- State that the trace explains tool use and evidence, not private chain-of-thought.
- Show the preserved write-approval path separately.
- Keep benchmark evidence available for any performance claim.

### 15:00–17:00 — Teardown rehearsal or final cleanup

```bash
export CONFIRM_DESTROY=hpe-agentic-remediation-demo
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
make destroy-all
```

Confirm the command reports empty application Terraform state and no tagged non-KMS residuals. KMS key records remain only in AWS pending deletion. If another demo is required, redeploy from the same reviewed commit after teardown succeeds.
