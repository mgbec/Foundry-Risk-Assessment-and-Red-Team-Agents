# A2A Threat Model & Gap Analysis (MAESTRO)

Threat model for the Agent-to-Agent (A2A) communication in this project,
using the Cloud Security Alliance's **MAESTRO** framework for agentic AI.

- Framework source: [Threat Modeling Google's A2A Protocol with the MAESTRO Framework](https://cloudsecurityalliance.org/blog/2025/04/30/threat-modeling-google-s-a2a-protocol-with-the-maestro-framework) (CSA, Ken Huang & Dr. Idan Habler, 2025-04-30).
- MAESTRO = Multi-Agent Environment, Security, Threat, Risk, and Outcome — a seven-layer, AI-specific threat modeling approach.
- This document maps that framework's threats onto **this repo's actual A2A implementation** and records where we are covered, partially covered, or have a gap.

> Content in the "framework" columns is rephrased/summarized from the CSA
> article for licensing compliance. See the source for the full text.

## What's in scope

Two A2A paths exist in this project:

1. **Outbound** — the Foundry agent calls the AWS-hosted **SCF Compliance
   Assessment Agent**.
   Code: `agent/a2a_collaborator.py`, `infra/a2a_connection.tf`.
   Transport: Foundry `A2APreviewTool` → AWS API Gateway `POST /entra/rpc`
   (JSON-RPC 2.0), Entra-JWT authenticated via the Foundry project managed
   identity. Non-streaming async task model (submit → poll `tasks/get`).

2. **Inbound** — an external caller invokes **our** target agent.
   Code: `agent/a2a_target_agent.py` (Prompt agent with incoming A2A + Agent
   Card enabled). Gated by the `Foundry Agent Consumer` role.

### How our stack differs from the framework's assumptions

The MAESTRO article models **Google's A2A** assuming an Agent Card at
`/.well-known/agent.json`, `tasks/send`, SSE streaming, and **DID-based**
identity. Our stack instead uses **Foundry's A2APreviewTool + AWS API
Gateway JWT (OAuth2 / OIDC) authorizers**, a **non-streaming async task
model**, and **Entra / Cognito bearer tokens** rather than DIDs. Several
DID-specific mitigations therefore don't map 1:1; where our JWT model is an
acceptable substitute versus a genuine gap is called out per threat.

## Status summary

| Layer | Posture | Headline |
|-------|---------|----------|
| 1 Foundation Models | Partial | SCF-side Bedrock Guardrail helps; no output verification or rate limiting on our side. |
| 2 Data Operations | Gap | No validation / provenance tracking on A2A message parts in either direction. |
| 3 Agent Frameworks | Gap | No message signing, card signature verification, or mTLS; trust rests on URL pinning + TLS. |
| 4 Deployment & Infra | Mostly inherited | AWS-managed scaling/throttling; no client-side rate limiting. |
| 5 Evaluation & Observability | Partial | Good tamper-resistant plumbing; tracing opt-in, no integrity monitoring / anomaly detection. |
| 6 Security & Compliance | Strongest | No stored secrets; managed identity + app role; strict token validation. |
| 7 Agent Ecosystem | Low exposure | Point-to-point, two agents; ecosystem threats premature. |

## Layer 6 — Security & Compliance (strongest area)

| Threat | Status | Evidence / gap |
|--------|--------|----------------|
| **T6.1** Unauthorized access to agent credentials | Covered | No stored secrets: managed identity + `Agent.Invoke` app role; `local_auth_enabled=false` on the Foundry account; storage `shared_access_key_enabled=false`; AAD-authenticated App Insights ingestion. Satisfies M6.1.1 ("never embed credentials"). |
| **T6.3** Abuse of delegated authority | Partial | Strict token validation (API Gateway checks `aud` + `iss`); the app role scopes *who* may call. **Gap:** `Agent.Invoke` is all-or-nothing — no per-action authorization once a caller is admitted. Auditing partial (see L5). |
| **T6.2** Compliance on sensitive data (PII) | Gap (inbound) | `a2a_target_agent.py` exposes `lookup_customer_account` / `reset_customer_password` over A2A (name, email, balance). Synthetic today, but no PII redaction / minimization (M6.2.1–2) on the A2A response path — a real gap if pointed at live data. |

## Layer 3 — Agent Frameworks (the A2A protocol itself) — biggest real gaps

| Threat | Status | Evidence / gap |
|--------|--------|----------------|
| **T3.4** Malicious server impersonating a trusted company | Partial | We pin the exact card URL (`/entra/.well-known/agent-card.json`) and use TLS to a fixed `execute-api` host, blunting casual spoofing. **Gaps:** no cryptographic Agent Card verification (M3.4.6), no mTLS (M3.4.3), no card signature / Certificate Transparency (M3.4.2), no agent registry (M3.4.5). Trust rests on "we hardcoded the right URL and TLS is valid." Reasonable to risk-accept for a fixed known partner; still a gap vs. the framework. |
| **T3.1** Unauthorized agent impersonation | Partial | Inbound is gated by the `Foundry Agent Consumer` role. Outbound, AWS validates our JWT. The framework wants **DIDs + mutual auth** (M3.1.1–2); we have one-directional bearer-token auth. **Gap:** the SCF agent authenticates *us*, but we don't cryptographically authenticate *it* beyond TLS. |
| **T3.2** Message injection | Gap | No digital signatures on A2A messages (M3.2.1); no input validation / content filtering on message parts in either direction (M3.2.2–3). The collaborator forwards whatever the model produces and ingests whatever the SCF agent returns. |
| **T3.3** Protocol downgrade | Covered enough | Transport fixed to HTTPS JSON-RPC to a known endpoint; no negotiation surface to downgrade. Low relevance here. |

## Layer 2 — Data Operations

| Threat | Status | Gap |
|--------|--------|-----|
| **T2.1** Data poisoning via message parts | Gap | The SCF agent's response feeds back into the Foundry agent's context with no validation / provenance tracking (M2.1.1, M2.1.3). This is the concrete instance of the article's **C3.1 cross-layer** attack (injected message → autonomous data action). |
| **T2.2** Sensitive information disclosure | Partial | Outbound prompts to the SCF agent could carry sensitive context; no egress filtering. Observability content-capture being **off by default** is a good L2 control for the logged copy of that data. |

## Layer 5 — Evaluation & Observability

| Threat | Status | Evidence / gap |
|--------|--------|----------------|
| **T5.1** Log manipulation / integrity | Partial | Solid plumbing: OTel → App Insights with AAD auth (tamper-resistant ingestion) and AWS API Gateway access logs (our forensic lifeline while debugging — M3.4.9 / M5.1 in action). **Gaps:** no log-integrity monitoring / checksums (M5.1.2); no anomaly detection (M5.1.3, M1.2.2); tracing is opt-in (off unless `APPLICATIONINSIGHTS_CONNECTION_STRING` is set), so by default there's no persistent trail of A2A calls on the Foundry side. |

## Layer 1 — Foundation Models

| Threat | Status | Gap |
|--------|--------|-----|
| **T1.1** Message-generation / evasion | Partially external | The SCF agent applies a Bedrock Guardrail (prompt-attack + PII filtering) on its side. On our side, no output verification (M1.1.2) of what comes back before it enters our agent. |
| **T1.2** Model extraction | Gap | No rate limiting or anomaly detection on outbound calls (M1.2.1–2). The SCF stack has an SQS concurrency cap, but nothing throttles our agent from hammering it. |

## Layer 4 — Deployment & Infrastructure

| Threat | Status | Notes |
|--------|--------|-------|
| **T4.1** Denial of service | Mostly inherited | AWS API Gateway + Lambda + SQS provide managed scaling / throttling server-side. No client-side rate limiting (overlaps M1.2.1). Reasonable at this scale. |

## Layer 7 — Agent Ecosystem

| Threat | Status | Notes |
|--------|--------|-------|
| **T7.1** Malicious agent interaction | Low exposure | Two-agent, point-to-point topology, not a marketplace. Reputation systems / sandboxing (M7.1.2–3) are premature. Revisit if the orchestrator fans out to multiple external agents (README "Wire it into the real pipeline"). |

## Cross-layer

- **C3.1 (Agent Frameworks → Data Operations):** an injected/wrong SCF
  response (L3) flowing unvalidated into the Foundry agent's reasoning and
  driving an autonomous data action (L2). This is the single most relevant
  cross-layer chain for our setup and is addressed by priorities 1–2 below.

## Prioritized remediation

Ranked by risk-to-effort for this specific two-agent setup:

1. **Input/output validation on the A2A boundary (T3.2, T2.1).** Highest
   value, lowest effort. Validate / schema-check and content-filter what
   leaves the collaborator and — more importantly — what returns from the
   SCF agent before it re-enters the agent's context. Closes the C3.1 path.
2. **Make observability non-optional for A2A calls (T5.1, T6.3).** Tracing
   is off unless a connection string is set. Wire `trace_run` into
   `a2a_collaborator.py` and consider defaulting it on for A2A paths so
   every delegation is auditable.
3. **Agent Card verification for the outbound target (T3.4 / T3.1).** We pin
   the URL; add a check that the resolved card's fields (name, url,
   provider) match expected values — cheap defense-in-depth against a
   repurposed endpoint. Full mTLS / DID is likely overkill for a fixed known
   partner; reasonable to risk-accept and document.
4. **PII handling before the inbound target leaves synthetic data (T6.2,
   T2.2).** A "before you go live" gate, not urgent while data is synthetic.
5. **Client-side rate limiting (T1.2, T4.1).** Low urgency at two-agent
   scale.

## Scope and limitations

This is a **design-level** gap analysis mapping the CSA/MAESTRO threats to
code and configuration reviewed in this repository. It is **not** a
penetration test or a formal audit. The identity / auth controls were
verified empirically during setup (the observed 401 → 200 progression in the
API Gateway logs); statements such as "no input validation" reflect the
current source as read, not runtime testing. Some framework mitigations
(DIDs, Certificate Transparency logs, agent registries) are aspirational for
the broader A2A ecosystem and not standard in Foundry / AWS today — those are
flagged as reasonable risk-acceptances rather than actionable gaps.
