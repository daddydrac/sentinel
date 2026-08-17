# Feedback-to-Demo Acceptance Matrix

The screenshot reads:

> **Interview Feedback (Not Selected)**
>
> The candidate demonstrated some awareness of AI concepts and terminology; however, the interview did not provide sufficient evidence of the hands-on AI and agentic systems experience required for this role.
>
> **Key Observations:**
>
> - Was unable to effectively design an agent-based solution or clearly articulate the architecture, reasoning workflow, tool orchestration, decision-making process, and execution patterns involved in modern agentic systems.
> - Demonstrated limited understanding of retrieval architectures and was unable to clearly explain the differences, trade-offs, and appropriate use cases for Vector RAG, Graph RAG, and Hybrid RAG.
> - Showed limited familiarity with guardrails, including how they are implemented to enforce safety controls, authorization boundaries, approval workflows, and operational governance within production AI systems.
> - Struggled with AI architecture and solution design discussions, particularly around how tool calling, RAG, and guardrails would work together in a practical end-to-end implementation.
>
> **Decision:** Not selected to move forward. The candidate's demonstrated experience and interview performance did not align with the level of practical AI, agentic architecture, and production AI expertise required for the role.

The demo must provide observable evidence against every observation.

| Feedback gap | Designed and developed proof | Deployment proof | Live demo moment | Pass condition |
|---|---|---|---|---|
| Agent architecture and reasoning | Bedrock Agent reasoning plus explicit `INTAKE -> RETRIEVE -> PLAN -> POLICY -> APPROVAL -> EXECUTE -> VERIFY -> COMPLETE/COMPENSATE` state machine | Bedrock Agent/alias/action group and Step Functions Standard with Lambda tasks and callback token | Show the agent trace, then walk the state machine left to right | Agent reasoning is visible; every durable state has input/output and a trace |
| Tool orchestration and execution patterns | Schema-defined MCP tools, canonical arguments, risk, rollback, idempotency key, and receipt | AgentCore Gateway target, read-only bridge allowlist, and separately authorized workflow role | Show an autonomous read call, then approve one exact plan and show one receipted write | Agent cannot call writes; a modified hash is rejected; retries cannot create another logical action |
| Vector RAG | Deterministic normalized vectors and cosine ranking over incidents/runbooks | Same Lambda artifact in AWS | Show semantically similar incident even when wording differs | Semantic result set is visible and scored |
| Graph RAG | Typed two-hop traversal over host, workload, dependency, owner, policy | Same governed fixture deployed with the worker | Show host -> service -> database impact path | Paths and hop counts are visible; no model-generated edge becomes authority |
| Hybrid RAG | BM25-like + vector + graph + current state assembled under one manifest hash | Evidence manifest written to encrypted S3 | Compare four branches and the unified evidence IDs | Plan cites the immutable evidence manifest |
| Guardrails and authorization | Source trust labels, Bedrock input/output filters, closed tool/plan schemas, bridge allowlist, deterministic `ALLOW/DENY/REQUIRE_APPROVAL`, and obligations | Versioned Bedrock Guardrail; separate Bedrock, MCP bridge, workflow, API, and tool roles; KMS; private S3 | Show the Bedrock Agent limited to reads and a medium-risk write pausing for approval | Unsafe model input/output is filtered; no model-facing path can invoke a write; no workflow write bypasses policy/approval |
| Approval workflow | Plan hash, task token, expiry, exact decision | Step Functions `.waitForTaskToken`, token held server-side in DynamoDB | Approve the displayed hash; optionally attempt a tampered hash | Tampered hash returns HTTP 409 |
| Operational governance | Trace, CloudWatch logs, evidence manifest, policy version, receipt, verification | CloudWatch, DynamoDB PITR/TTL, S3 versioning | Open trace and show who/what/why/outcome fields | The run is reconstructable end to end |
| RAG + tools + guardrails together | One workflow contract from autonomous MCP retrieval through verified outcome | Terraform provisions Bedrock Agent, AgentCore MCP, the workflow, data plane, and observability | Run success and compensation scenarios | Successful action verifies; failed outcome compensates rather than blindly retries |

## Interview explanation in one paragraph

“I built a durable control plane around probabilistic reasoning. A Bedrock Agent autonomously chooses read-only tools exposed through an IAM-authenticated AgentCore MCP Gateway. Hybrid RAG retrieves exact errors, semantically similar incidents, governed dependency paths, and current state. The plan is schema-valid and bound to an evidence-manifest hash. Deterministic policy decides whether the action is denied, allowed, or requires approval. Approval is bound to the immutable plan hash, the executor produces an idempotent receipt, and an independent adapter verifies the outcome. If verification fails, the workflow compensates and escalates; the model never owns identity, write authority, credentials, or the final claim of success.”
