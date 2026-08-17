# Production Migration Map

The demo deploys the complete guarded control path, including Bedrock Agent, AgentCore MCP, Step Functions, and EMR Serverless. The remaining migrations replace demonstration adapters; they do not redesign the evidence, plan, policy, receipt, or verification contracts.

| Order | Demo adapter | Production target | Promotion gate |
|---:|---|---|---|
| 1 | Managed public HDFS acquisition and synthetic 100 GiB corpus | Governed GreenLake/OpsRamp telemetry in S3/Parquet + cataloged ingestion | Three complete runs, zero silent loss, lineage and freshness SLOs |
| 2 | OpenSearch lexical retrieval over static HDFS patterns | Production index lifecycle, temporal fields, tenant routing, and calibrated analyzers | Exact-ID Recall@20 ≥0.95 and tenant-isolation tests |
| 3 | Nova L2 embeddings with OpenSearch FAISS/HNSW | Evaluated domain embedding/version lifecycle | Semantic Recall@20 ≥0.90, drift gates, and latency SLO |
| 4 | Neptune HDFS block/template/host/process graph | Governed topology and operational event graph | Path validity ≥0.99 plus freshness and tenant-isolation tests |
| 5 | Lambda policy function | Verified Permissions/Cedar policy store | Golden policy suite and zero unauthorized writes |
| 6 | AgentCore MCP simulated lifecycle tools | Approved GreenLake/OpsRamp MCP adapters with narrow execution roles | Contract, idempotency, canary, compensation, and break-glass tests |
| 7 | Lambda control plane | Spring Boot services on ECS/Fargate, if sustained traffic warrants it | 250-user soak, fault injection, and recovery tests |
| 8 | Cognito-native demo users plus legacy workflow token | IAM Identity Center/enterprise IdP federation, MFA, and removal of legacy token | RBAC/ABAC, revocation, joiner/mover/leaver evidence, and separation of duties |

Do not combine these migrations into one release. Each replacement must reproduce the demo contract tests and trace fields before promotion.
