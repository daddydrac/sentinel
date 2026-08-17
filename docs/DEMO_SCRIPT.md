# GraphRAG Demo Script

## Before the audience joins

1. Confirm `terraform -chdir=infra output -raw graphrag_ui_url` opens.
2. Confirm the presenter is signed in as `approver` or `admin` and has refreshed tokens after any group change.
3. Retain the successful benchmark JSON and S3 run manifest.
4. Confirm `slo_passed` is true; otherwise describe 600 seconds as the target, not an achieved result.
5. Run one chat to warm the SageMaker endpoint and confirm streaming.
6. Keep the preserved remediation UI available in a second tab.

## Seven-minute walkthrough

### 0:00–1:00 — Business problem

“Large HDFS estates produce repetitive logs. The operator needs a short list of the most important deviations, concrete evidence across log and topology data, and actions a human can safely take—not an opaque answer or an autonomous write.”

### 1:00–2:00 — Data proof

Show the benchmark JSON and run manifest. Explain that acquisition and corpus generation are outside the timed gate. The formal run physically reads at least 100 GiB, indexes every record’s metadata, embeds unique patterns once, loads the graph, reconciles counts, publishes aliases, and passes a live MCP query within 600 seconds.

### 2:00–4:00 — Live investigation

Ask:

> Identify the three most important anomalous HDFS log behaviors and give me an action plan.

Pause when the in-chat approval card appears. Explain that Bedrock Agent selected the displayed read-only MCP functions autonomously, but its `RETURN_CONTROL` action group prevented execution. Review the exact arguments, reason, expiration, and plan hash, then approve the plan in chat. If a second selection round appears, approve its new hash independently. For the rejection branch, start a new chat and reject the plan to prove that none of its selected MCP calls execute.

As the answer streams, use the 25% left panel to show:

- Bedrock Guardrail input acceptance;
- Bedrock Agent’s bounded MCP choices;
- lexical and Nova vector retrieval;
- Neptune block/event expansion; and
- the exact top-three ranking gate.

State explicitly that this is a public tool/evidence trace, not private model chain-of-thought.

### 4:00–5:30 — Findings and actions

For each card, show the deterministic rank, unsupervised signal, event codes, occurrence count, store citations, and three operator actions. Explain that dataset labels were held out of retrieval/ranking and remain available only for offline evaluation.

### 5:30–6:30 — Preserved safety path

Switch to the original remediation UI. Show that GraphRAG recommends actions but cannot execute them. Any actual write still requires the existing evidence-bound plan, deterministic policy, exact-plan approval when required, executor revalidation, idempotent receipt, independent verification, and compensation on failure.

### 6:30–7:00 — Cost and cleanup

Show `make destroy-all`. Explain the account, Region, and confirmation gates; saved destroy plan; remote-state-last order; tagged residual scan; and seven-day AWS KMS pending-deletion constraint.

## Claims to avoid

- Do not claim the 600-second target without a successful target-account run.
- Do not describe the public trace as chain-of-thought.
- Do not claim actions were executed from the GraphRAG chat.
- Do not imply dataset labels drove anomaly detection.
- Do not call Cognito-native demo users a complete enterprise identity lifecycle; production still needs federation/MFA and access governance.
