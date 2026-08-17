# Business and Engineering Implementation Report

## Executive assessment

The reconciled codebase is a unified AWS reference implementation for HDFS-log anomaly investigation with GraphRAG and governed human authority. The legacy agentic-remediation system remains present. Its Guardrail, Bedrock Agent, AgentCore MCP boundary, deterministic policy, exact-plan write approval, executor revalidation, independent verification, compensation, evidence hashes, and durable audit state were not replaced.

The old data processor, which expanded Parquet and computed distributed counts/fingerprints, was replaced. The new managed ingestion path turns a physical 100 GiB HDFS corpus into coordinated query-ready stores:

- OpenSearch holds occurrence metadata and L2-normalized Nova pattern embeddings using FAISS/HNSW.
- Neptune holds run-scoped HDFS blocks, templates, events, sources, replication groups, hosts, DataNodes, processes, anomalies, evidence, types, actions, and their relationships.
- A dedicated read-only MCP Lambda exposes eight bounded GraphRAG functions.
- A Cognito-authenticated Amplify chat requires an `approver` or `admin` to accept each exact Bedrock Agent-selected tool batch before execution.

The code is locally verified but not live-AWS certified. No AWS account was available in this development environment, and Terraform itself was unavailable here. The architecture must therefore pass authenticated `terraform validate/plan`, a full target-account deployment, browser/API smoke tests, and three actual 100 GiB runs before it is represented as production-qualified or as having achieved the ten-minute objective.

## Business purpose

Large operational logs contain substantial repetition and a small number of weak, distributed anomaly signals. Manual inspection is slow, while an unrestricted LLM diagnosis is difficult to audit and unsafe to treat as execution authority. This solution assigns different responsibilities to purpose-built components:

| Business responsibility | Component | Why it exists |
|---|---|---|
| High-volume transformation | EMR Serverless Spark | Parallel processing without maintaining a permanent cluster |
| Semantic and exact retrieval | OpenSearch | Combines operator terms/event codes with vector similarity |
| Relationship context | Neptune | Grounds findings in blocks, templates, events, hosts, processes, evidence, and actions |
| Bounded autonomous selection | Bedrock Agent | Chooses from a closed read-tool catalog |
| Human authority | Cognito group + exact-plan approval | Prevents autonomous selection from becoming autonomous access/execution |
| Content safety | Bedrock Guardrail | Screens both input and output |
| Fast answer synthesis | Pinned open-weight Qwen on SageMaker | Streams from an account-owned, public, non-gated Apache-2.0 artifact |
| Operator experience | Amplify + AppSync Events | Provides authenticated managed hosting and progress/result streaming |
| Operational change control | Preserved remediation workflow | Keeps deterministic policy and verified write execution separate from diagnosis |

The business result is an evidence-led demonstration: the assistant accelerates prioritization and explanation, while humans and deterministic controls retain authority.

## Reconciliation result

The old repository root was kept as the base. The nested `HPC_Autonomous_Agents_2` work was reconciled into that root rather than shipped as a second application. Duplicate project nesting and the obsolete `hpc/analyze_100g.py` processor were removed. Original local scenarios, workflow code, APIs, tests, contracts, and remediation UI remain in place.

The new modules and services are root-level peers of the legacy system, sharing the encrypted evidence/state foundation only where appropriate. GraphRAG data access is deliberately implemented in a separate Lambda and AgentCore target, so the legacy action-group Lambda does not acquire OpenSearch, Neptune, or Nova permissions.

## Data acquisition and 100 GiB construction

The source dataset is pinned to an immutable 40-character Hugging Face revision. A Terraform-managed CodeBuild project runs `scripts/acquire_hf_dataset.py` with no developer workstation dependency. The script:

1. reads the repository tree at the exact revision;
2. requires the expected Parquet count;
3. validates the dataset card's MIT license;
4. downloads each file as a stream while calculating SHA-256;
5. enforces available content/LFS hashes;
6. uploads the immutable source objects to S3; and
7. writes the `READY` acquisition manifest last.

EMR Serverless then runs `hpc/generate_100g.py`. It preserves typed source fields and generates deterministic replicas plus a per-row non-repeating synthetic payload. Parquet output is uncompressed and written with a margin above 100 GiB. This is a benchmark corpus: the payload proves physical data processing throughput but is never copied into the online stores.

The formal GraphRAG job starts only after S3 physical bytes are independently validated. Its first Spark action aggregates `length(synthetic_payload)` and fails below 107,374,182,400 bytes. This prevents Spark column pruning from making the test appear to process 100 GiB while reading only a small logical projection.

## Pattern derivation, privacy, and label isolation

Each expanded occurrence gets a deterministic ID derived from immutable revision, HDFS block, replica, and source hash. Ordered Drain event codes form a canonical semantic template and deterministic pattern ID. A bounded representative preview masks raw IP and large numeric values.

Host and DataNode addresses are converted to revision-scoped SHA-256 identifiers. The graph and online indexes therefore correlate infrastructure behavior without exposing the source address string. DataNode port `50010` becomes a typed process relationship.

The source `label` is treated as evaluation-only. It is written under the run's isolated evaluation prefix and is absent from:

- embedding text and Nova requests;
- OpenSearch mappings and documents;
- Neptune nodes and edges;
- online retrieval and ranking functions.

The interpretable demo score is:

\[
score = 0.65 \times rarity + 0.35 \times structural\ deviation
\]

This is useful for explaining the demonstration but is not a production anomaly model. A production program should evaluate held-out labels offline and add temporal, topology, and live-telemetry features without weakening label isolation.

## Embeddings and OpenSearch

Nova is called once per unique semantic pattern, rather than once per replicated occurrence. Every response must have the configured dimension, contain finite values, and have non-zero magnitude. The code records the pre-normalization norm, applies L2 normalization, recomputes the post-normalization norm, and fails outside tolerance.

The run-scoped pattern index uses:

- OpenSearch `knn_vector`;
- 1,024 dimensions by default;
- FAISS engine;
- HNSW method;
- `innerproduct` space over unit vectors, giving cosine-equivalent ordering; and
- managed remote/GPU index-build settings from the benchmark profile.

The record index contains every expanded occurrence's retrieval metadata, provenance, block, replica, pattern, host/process identifiers, anomaly score, run, tenant, and classification. The pattern index contains the unique patterns, event codes, host/process identifiers, embedding lineage, vector, scores, and graph entity IDs.

Distributed signed bulk writes collect client totals. OpenSearch server `_count` values must exactly match. Both indexes must return to green serving health. Stable aliases move only after the graph has also loaded and validated.

## Neptune graph and publication

Every graph ID is prefixed with the pipeline run, preventing cross-run entity collisions. Node types are:

- `PipelineRun`, `HDFSBlock`, `LogTemplate`, `LogEvent`;
- `SourceRecord`, `ReplicationGroup`;
- `Host`, `DataNode`, `Process`;
- `Anomaly`, `Evidence`, `AnomalyType`, `RecommendedAction`.

Relationships cover source-to-block/template/host/process provenance, template-to-event composition, evidence-to-source and evidence-to-anomaly support, anomaly-to-block/host impact, run lineage, replication expansion, host/process placement, anomaly typing, and recommended human action.

Every node and edge carries pipeline run, dataset revision, classification, tenant, valid-from time, and provenance. Spark computes the expected number of CSV graph records. Neptune bulk loading runs with failure-on-error and `NEW` mode; the loader total must exactly equal the expected count.

Before publication, openCypher checks that every run anomaly connects to evidence, an affected block, and the pipeline run, and that every anomaly has a provenance path through its evidence to a source record. Only then are OpenSearch aliases moved and the immutable `PUBLISHED` manifest written. A partial build cannot become the active read run.

## Managed orchestration and the ten-minute gate

Terraform creates a Standard Step Functions state machine and fail-closed gate Lambda. A DynamoDB conditional lease allows one ingestion run at a time. The workflow sequences acquisition, acquisition validation, corpus generation, physical-size validation, GraphRAG build, publication validation, and a real top-three read-tool smoke query. All failure branches attempt lease release and terminate failed.

The accepted performance field is the Spark-produced `elapsed_seconds_inside_spark`. It includes physical staged-data scan, transformation, Nova embedding, OpenSearch writes/health/counts, Neptune artifact generation/load/validation, alias movement, and manifest publication. It excludes infrastructure provisioning, public acquisition, 100 GiB generation, model build/download/startup, and the client wrapper's wait time; those are reported separately.

The pipeline fails when the Spark value exceeds 600 seconds. This makes the threshold executable, but code cannot guarantee capacity, quotas, external service latency, or pattern cardinality. Qualification requires three successful target-environment runs with preserved manifests.

## GraphRAG query and MCP tools

The dedicated MCP surface exposes only these closed-schema read functions:

| Tool | Purpose |
|---|---|
| `search_log_events` | Bounded lexical, vector, or hybrid pattern retrieval |
| `query_hdfs_graph` | Bounded one/two-hop, run-isolated graph expansion |
| `get_anomaly_evidence` | Stored features, graph rows, lineage, and evidence hash |
| `correlate_block_failures` | High-scoring occurrence correlation for named blocks |
| `analyze_node_behavior` | Sample-size-aware behavior summary |
| `rank_anomalies` | Deterministic hybrid ranking with exactly three for the demo |
| `generate_remediation_plan` | Bounded human actions, never execution authority |
| `validate_remediation` | Honest validation status against the static snapshot |

Hybrid retrieval uses reciprocal-rank fusion over lexical and Nova vector candidates plus a small deterministic stored-anomaly contribution. Graph queries resolve the active OpenSearch run and filter Neptune paths to the same run. Missing service configuration or a store failure stops the request; there is no GraphRAG fixture fallback.

## Authentication, users, and authorization

Terraform creates an invitation-only Cognito pool, a no-secret UI client, 15-minute ID/access tokens, strong temporary/permanent password policy, and three groups:

| Group | Effective authority |
|---|---|
| `investigator` | Create and read owner-bound chats; cannot decide a tool plan |
| `approver` | Investigator capabilities plus approve/reject exact GraphRAG plans |
| `admin` | Administrative demo role and the same in-chat decision authority |

`make deploy` requires an initial operator email and provisions `admin` by default. `make add-user` and `make add-users` perform idempotent Cognito invitations and exact managed-group reconciliation after verifying the AWS account and Region. This approach avoids placing temporary passwords in Terraform state.

API Gateway applies a Cognito JWT authorizer only to `/api/chats*`. Chat rows store the token subject and cross-subject reads/decisions return not found. The decision API additionally requires the `approver` or `admin` group claim. AppSync Events uses Cognito subscriptions and IAM-only server publication; its namespace handler restricts subscriptions to the authenticated subject's chat channel. The browser contains no AppSync API key and no legacy demo token.

Legacy remediation endpoints retain their disposable `x-demo-token` so existing functionality remains operational. That token is not accepted as chat authorization.

## Human-in-the-loop agent behavior

The dedicated GraphRAG Bedrock Agent action group uses `RETURN_CONTROL`, so the Agent cannot directly execute its selected tools. The worker canonicalizes each selected call, rejects undeclared tools/arguments and query rewriting, applies cardinality limits, binds the plan to chat ID, exact operator query, invocation, round, expiry, and SHA-256 hash, persists it, and stops.

Only the owning `approver` or `admin` can submit the decision. The API conditionally updates a chat that is still awaiting that same hash. On approval, the worker reloads and validates the exact plan before the first MCP call. Rejection, expiry, duplicate decision, tampering, cross-chat reuse, or mutation fails closed. A new Agent-selected batch creates a new hash and another checkpoint.

The separate preserved remediation workflow still owns write-tool policy and approvals. GraphRAG recommendations are actions for a human, not autonomous remediations.

## Chat UI and answer path

The Amplify-hosted React UI uses Cognito's managed sign-in and new-password flow. Desktop layout is a 25/75 grid and becomes stacked on narrow screens. The left panel reports public Guardrail, tool-selection, approval, MCP, and evidence events. It explicitly disclaims private chain-of-thought.

The main workspace shows:

- question and run status;
- exact-plan approval card;
- token-streamed synthesis;
- exactly three ranked finding cards;
- severity/confidence signal, event and block chips;
- probable cause, citations, graph evidence, limitations; and
- three numbered actions for a human per finding.

The UI subscribes before submitting the chat and polls DynamoDB-backed state for recovery. Server publication to AppSync is IAM-signed. Input and output are passed through the preserved Bedrock Guardrail.

On completion, the worker writes an encrypted, versioned final-answer/evidence JSON object under the owning Cognito subject and chat ID in S3. DynamoDB stores the public result together with that S3 URI and SHA-256, providing both low-latency recovery and durable evidence lineage.

## Infrastructure, cost, and teardown

OpenSearch, Neptune, EMR workers, and the GraphRAG tool Lambda use the private data network. OpenSearch and Neptune use IAM/SigV4 in addition to VPC controls. The application uses encrypted S3/DynamoDB, log retention, throttling, WAF managed/rate rules, immutable model and dataset revisions, and a visible budget.

`make destroy-all` requires exact confirmation plus expected account and Region. It applies a saved destroy plan, checks Terraform application state is empty, destroys the optional remote-state stack last, removes only generated local backend/runtime files, and queries tagged residual resources in the workload and global-service Regions. Non-KMS residuals fail the command. KMS key records can remain in their AWS-mandated pending-deletion period and are unusable during that window.

## Verification status and residual risks

Locally completed:

- 36 Python unit, workflow, policy, tool, approval, identity, ingestion, and UI contract tests;
- Python compilation and shell syntax checks;
- TypeScript no-emit checking and a production Vite build;
- JSON/tool-registry checks exercised by tests;
- source-level FAISS/HNSW, L2, label isolation, run isolation, Cognito, approval, managed ingestion, and teardown gates.

Not completed in this environment:

- Terraform CLI formatting/provider validation or an authenticated plan;
- AWS service/API deployment;
- Cognito invitation and browser sign-in;
- AgentCore/Bedrock return-control integration;
- AppSync Event delivery;
- OpenSearch and Neptune live loading/query behavior;
- SageMaker model build/start/stream latency;
- actual 100 GiB duration and three-run reproducibility;
- live destroy/residual verification.

The largest technical risk is the ten-minute target: 100 GiB physical scan, tens of millions of occurrence documents, Nova request throughput, OpenSearch indexing/health, and Neptune artifact/load/validation all occur inside the measured Spark job. The configured profile is a reasoned starting point, not a guarantee. Capacity rehearsal and manifest-backed tuning are mandatory.

## Go-live recommendation

Use this repository first as a controlled benchmark/demo environment. Before an external demonstration:

1. run Terraform validation and review a saved plan in the target account;
2. validate quotas and model/service access;
3. deploy and test admin, approver, and investigator behavior;
4. run three full 100 GiB qualifications and retain manifests/metrics;
5. execute Guardrail, rejection, tamper, expiry, and cross-user tests;
6. rehearse the original remediation scenarios; and
7. execute `make destroy-all`, confirm its gates, then redeploy for the event.

For production beyond a demo, add enterprise federation/MFA, tenant-aware index/graph authorization, security monitoring, calibrated anomaly evaluation, live post-remediation telemetry, backup/recovery objectives, incident procedures, and performance/load qualification based on the true pattern and occurrence cardinalities.
