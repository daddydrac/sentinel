# Sentinel — Quick Start

Commands only. Background and design decisions are in [README.md](README.md).

Use a dedicated disposable AWS account. The `dev` profile costs roughly
**$2–3/hour**, dominated by the SageMaker GPU endpoint.

## 1. Authenticate and set safety gates

Requires Terraform 1.10+, AWS CLI v2, Python 3.11+, Node/npm, `make`, `curl`,
`jq`, `zip`.

```bash
aws sso login --profile YOUR_PROFILE
export AWS_PROFILE=YOUR_PROFILE
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
export INITIAL_OPERATOR_EMAIL=primary.admin@example.com
export INITIAL_OPERATOR_GROUPS=admin

aws sts get-caller-identity
```

Never put credentials or user files in source control or `.tfvars`.

## 2. Deploy

```bash
make test        # 63 tests, no AWS needed
make preflight   # account/region/quota checks
make deploy      # TFVARS=dev by default
```

`make deploy` runs both Terraform phases, resolves the immutable model image
digest, invites the first operator, runs the ingestion state machine, and
publishes the UI. Use `AUTO_APPROVE=1 make deploy` only in a verified sandbox.

For the 100 GiB benchmark profile instead:

```bash
export CONFIRM_HPC_COST=100G-GRAPHRAG
make deploy TFVARS=demo
```

Get the URL:

```bash
terraform -chdir=infra output -raw graphrag_ui_url
```

Deploy pieces individually when iterating:

```bash
make deploy-infra   # infrastructure + model, no corpus ingestion
make deploy-ui      # UI only, ~1 minute
```

## 3. Add users

```bash
make add-user EMAIL=oncall.approver@example.com GROUPS=approver
make add-user EMAIL=platform.admin@example.com  GROUPS=admin
make add-users FILE=/secure/path/users.csv
```

`approver` and `admin` can accept or reject plans. `investigator` gets
owner-bound chat access but cannot approve.

**If the invitation email never arrives** — the pool uses Cognito's built-in
mailer, which is rate-limited and usually lands in spam (sender
`no-reply@verificationemail.com`). Set a password directly:

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id "$(terraform -chdir=infra output -raw cognito_user_pool_id)" \
  --username someone@example.com --password 'SomeStrong!Passw0rd#2024' --no-permanent
```

`--no-permanent` forces a change on first sign-in. Minimum 14 characters with
upper, lower, digit, and symbol.

## 4. Run the investigation demo

Sign in and ask:

> Identify the three most important anomalous HDFS log behaviors and give me an action plan.

The model selects read-only tools through Bedrock Converse and **stops**. The
card shows the exact tools, arguments, reason, expiry, and SHA-256 plan hash.
Choose **Approve exact plan**. Nothing reaches the MCP Gateway before that. If
the model asks for another batch, it needs its own approval.

The result is three findings, each with a colour-coded event-sequence strip, a
probable cause derived from that sequence, the Neptune path
(`pattern → block → host/process/template`), citations, and three actions.

The left panel is a public tool/authority trace — not hidden model reasoning.

## 5. Run the remediation demo

Success path, then compensation path:

```bash
API=$(terraform -chdir=infra output -raw demo_url)
TOKEN=$(aws lambda get-function-configuration \
  --function-name "$(terraform -chdir=infra output -raw api_function_name)" \
  --query 'Environment.Variables.DEMO_TOKEN' --output text)

# start: storage_path_regression | firmware_drift_diagnosis | verification_failure
curl -s -X POST "$API/api/workflows" -H "x-demo-token: $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"scenario_id":"storage_path_regression"}'
```

Poll until `AWAITING_APPROVAL`, then read `plan_hash`:

```bash
curl -s "$API/api/workflows/WORKFLOW_ID" -H "x-demo-token: $TOKEN"
```

Prove the gate rejects tampering, then approve for real:

```bash
# tampered hash -> HTTP 409 Plan hash mismatch
curl -s -X POST "$API/api/workflows/WORKFLOW_ID/decision" -H "x-demo-token: $TOKEN" \
  -H 'content-type: application/json' -d '{"approved":true,"plan_hash":"deadbeef"}'

# correct hash -> HTTP 202, then Execute -> Verify -> Complete
curl -s -X POST "$API/api/workflows/WORKFLOW_ID/decision" -H "x-demo-token: $TOKEN" \
  -H 'content-type: application/json' -d '{"approved":true,"plan_hash":"REAL_HASH"}'
```

Expected outcomes:

| Scenario | Ends at | Status |
|---|---|---|
| `storage_path_regression` | `Complete` | `VERIFIED`, with a receipt |
| `verification_failure` | `Compensate` | `COMPENSATED`, rolled back and escalated |
| `firmware_drift_diagnosis` | `Deny` | no safe automatic action |

The remediation scenarios are fixtures and label themselves `"simulated": true`.
The approval, policy, receipt, and verification machinery around them is real.

## 6. Re-run the benchmark

```bash
export CONFIRM_HPC_COST=100G-GRAPHRAG
make benchmark-100g
```

Output must include `query_ready_smoke_passed: true` and `slo_passed: true`.
`slo_passed` measures the staged-data Spark build only — not Terraform,
acquisition, corpus generation, or model startup. Keep three successful
manifests before calling the ten-minute objective reproducible.

## 7. Destroy

```bash
export CONFIRM_DESTROY=hpe-agentic-remediation-demo
export EXPECTED_AWS_ACCOUNT_ID=123456789012
export EXPECTED_AWS_REGION=us-east-1
make destroy-all
```

To cut most of the cost without a full teardown, delete just the GPU endpoint:

```bash
aws sagemaker delete-endpoint \
  --endpoint-name "$(terraform -chdir=infra output -raw sagemaker_endpoint_name)"
```

Findings, evidence, graph, and the approval flow keep working; only the final
prose synthesis stops. `make deploy-infra` brings it back.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Bedrock Agents is in Maintenance Mode` | Expected. The Converse path is the default; leave `enable_bedrock_agents = false` |
| UI loads, queries fail with `no such index` | Ingestion has not published yet. Check the ingestion state machine |
| `no executor being launched within 1200000ms` | Spark floor exceeds the capacity ceiling. Lower `hpc_spark_execution.min_executors` or raise `hpc_maximum_cpu` |
| `Session not initialized` from the MCP Gateway | The client must send `notifications/initialized` after `initialize` |
| `CUDA out of memory` on the endpoint | The synthesis prompt is too large. See `_synthesis_evidence` in `handlers/chat_handler.py` |
| A `-target` apply reports "No changes" unexpectedly | The address probably does not exist. Modules with `count` need `module.name[0]`. Check `terraform state list` first |
