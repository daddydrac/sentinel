# Reconciliation and Validation Report

## Scope

This report records how the legacy repository root and the nested `HPC_Autonomous_Agents_2` implementation were reconciled against `ENGINEERING_PLAN_GRAPHRAG_100G.md`. The result is one root-level codebase; there is no nested second application.

## Requirement traceability

| Requirement | Implemented location | Acceptance behavior |
|---|---|---|
| Preserve legacy Guardrail and governed remediation | `infra/modules/agentic`, `workflow`, `worker`; `lambda/app/*` | Original lifecycle/tests remain; GraphRAG approval grants no write authority |
| Managed dataset acquisition | `scripts/acquire_hf_dataset.py`; `infra/modules/analytics`; `scripts/stage_hf_data.sh` | Pinned revision, license/file/hash contract, S3 manifest written last |
| Physical 100 GiB corpus | `hpc/generate_100g.py` | Uncompressed deterministic corpus with non-repeating payload and S3 size gate |
| <=10 minute staged-data target | `hpc/graphrag_build.py`; `scripts/run_100g_benchmark.sh` | Fails over 600 Spark seconds; requires three AWS runs before claiming reproducibility |
| Nova embeddings and L2 | `hpc/graphrag_build.py`; `lambda/app/graphrag.py` | Dimension/finite/non-zero/unit-norm checks for indexing and query vectors |
| OpenSearch FAISS/HNSW | `hpc/graphrag_build.py`; `infra/modules/opensearch` | Strict vector mapping, run indexes, bulk/client/server reconciliation, green health, alias publication |
| Neptune graph | `hpc/graphrag_build.py`; `infra/modules/neptune` | Run-scoped typed graph, exact loader count, relationship/provenance openCypher gates |
| Automated end-to-end ingestion | `infra/modules/ingestion`; `lambda/app/pipeline_gate_handler.py` | Standard Step Functions, single-flight lease, failure unlock, publication and real top-three smoke gates |
| AgentCore MCP GraphRAG tools | `lambda/app/graphrag_tool_handler.py`; `contracts/graphrag-tools`; `infra/modules/agentic` | Separate read-only Lambda/role/target; eight closed schemas; legacy Lambda has no graph access |
| Human approval at least once | `lambda/app/tool_approval.py`, `chat_handler.py`, `api_handler.py`; UI | `RETURN_CONTROL`; every exact Agent-selected batch requires owner/role/hash-bound approval |
| Guardrails | `infra/modules/agentic`; `lambda/app/chat_handler.py` | Preserved Bedrock Guardrail on input and output |
| User and group deployment | `infra/modules/identity`; `scripts/provision_cognito_user*.sh`; `Makefile` | Invitation-only users; idempotent exact group reconciliation; approver/admin decision check |
| Authenticated streaming | `infra/modules/realtime`; `ui/src/main.tsx` | Cognito subject-bound AppSync subscription; IAM server publish; no browser API key |
| Final evidence durability | `lambda/app/chat_handler.py`; `infra/modules/chat` | KMS-encrypted S3 final JSON plus DynamoDB URI/SHA-256 |
| Amplify 25/75 UI | `ui/src/main.tsx`; `ui/src/styles.css`; `infra/modules/amplify_ui` | Public tool trace left, chat/approval/top three/actions right, responsive layout |
| Open-source default chat model | `infra/modules/model_serving`; `model/*`; `scripts/deploy.sh` | Public non-gated Apache-2.0 Qwen revision, account-owned build, immutable ECR runtime digest |
| One complete deployment | `make deploy`; `scripts/deploy_e2e.sh` | Infra/model → initial/bulk users → managed ingestion → UI |
| Complete teardown | `make destroy-all`; `scripts/destroy_all.sh` | Saved destroy plan, empty-state gate, remote-state-last, regional/global residual gate |

## Important design decisions

### Users are not Terraform user resources

Terraform creates the Cognito pool/client/groups. Post-apply scripts create users and assign roles. This keeps temporary credentials out of Terraform state while retaining account/Region verification and repeatable deployment commands. New users receive Cognito invitations. Existing managed role memberships are reconciled exactly, allowing reliable downgrade from `admin` to `approver`.

### GraphRAG and remediation tools use different Lambdas

The original action-group Lambda retains legacy permissions and tools. The HDFS graph functions use a separate read-only Lambda, VPC access, IAM data policy, and AgentCore target. This prevents adding broad OpenSearch/Neptune/Nova authority to the preserved remediation target.

### The UI explains tool use, not hidden reasoning

The left column contains public facts: Guardrail stage, selected tool, bounded reason, read-only authority, approval result, and result count. The implementation does not expose private chain-of-thought.

### Publication is run-scoped and fail-closed

Records, vector patterns, Neptune IDs, graph artifacts, evaluation labels, and manifests are run-scoped. The active aliases move only after both stores reconcile and graph queries prove required evidence/provenance relationships. The `PUBLISHED` manifest is the commit record.

## Local verification performed

| Check | Result |
|---|---|
| Python tests | 36 passed |
| Python compilation | Passed |
| Shell syntax | Passed |
| TypeScript no-emit check | Passed |
| Production Vite build | Passed; bundle-size warning only |
| Git whitespace/error check | Passed |
| Terraform CLI/provider validation | Not run; Terraform unavailable in this environment |
| Live AWS deployment | Not run; no target AWS account was available |
| 100 GiB/600-second qualification | Not run; must be measured in AWS |

## Mandatory target-account validation

The repository must not be called production-ready until all of the following pass:

1. `terraform fmt -check -recursive`, `terraform validate`, and a reviewed saved plan;
2. full `make deploy` in the exact account and Region;
3. Cognito new-password sign-in and refreshed group-token behavior;
4. investigator 403, approver/admin success, owner isolation, and AppSync channel isolation;
5. Guardrail intervention tests and plan rejection/tamper/expiry tests;
6. live AgentCore, OpenSearch, Neptune, Nova, SageMaker, and AppSync smoke tests;
7. three accepted 100 GiB manifests with `slo_passed=true`;
8. UI performance/accessibility and intended audience demonstration rehearsal; and
9. a complete `make destroy-all` run with empty Terraform state and zero tagged non-KMS residuals.

## Known limitations

- The 600-second target is executable as a gate but unproven. Actual throughput depends on AWS quotas, pattern cardinality, service capacity, and Region conditions.
- The default infrastructure is expensive and intended to be ephemeral.
- The anomaly score is an explainable unsupervised baseline, not a calibrated production detector.
- HDFS data is a static snapshot, so remediation validation cannot prove live recovery.
- Cognito native users are appropriate for the demo; enterprise production should use federation, MFA, lifecycle governance, and centralized access reviews.
- AWS KMS deletion has an unavoidable pending-deletion period after Terraform destroy.
