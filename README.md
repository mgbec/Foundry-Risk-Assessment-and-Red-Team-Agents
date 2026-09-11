# AI Foundry Risk Assessment + Red Teaming Agent

Provisions an Azure AI Foundry project (Terraform) and runs a two-stage
safety pipeline against it (Python): a fast non-adversarial **risk
assessment** pass, followed by an adversarial **AI Red Teaming Agent** scan
(Microsoft's PyRIT integration) — first against the raw model, then against
the agent built on top of it. Runs on a weekly schedule via GitHub Actions,
or on demand.

## Layout

```
infra/                       Terraform: RG, AI Foundry account + project,
                              model deployments, storage, Key Vault, RBAC
agent/
  config.py                  Env-driven settings shared by all scripts
  risk_assessment.py         Non-adversarial safety evaluators (fast gate)
  red_team_scan.py           PyRIT red team scan of the raw model (local)
  red_team_agentic.py        Cloud red team scan of the deployed agent
  orchestrator.py            Runs all three stages + uploads reports
  data/baseline_prompts.jsonl
.github/workflows/
  ai-safety-scan.yml         terraform apply -> python orchestrator.py,
                              scheduled weekly + manual trigger
```

## One-time setup

1. **Azure**: an OIDC federated app registration for GitHub Actions (no
   stored client secret) with `Contributor` on the target subscription/RG
   for the `terraform` job, scoped down after first apply if you prefer.
   Add its `client-id` / `tenant-id` / `subscription-id` as repo secrets:
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `AZURE_RESOURCE_GROUP`.

2. **Terraform variables** (`infra/terraform.tfvars`):
   ```hcl
   project_prefix                     = "aisafety"
   location                           = "eastus2"
   ci_service_principal_object_id     = "<object id of the OIDC app>"
   additional_scan_operator_object_ids = ["<your own object id, for local runs>"]
   ```

3. **Local dev**:
   ```bash
   cd infra && terraform init && terraform apply
   cd ../agent
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # fill in from `terraform output`
   az login
   python orchestrator.py
   ```

## What each stage actually checks

- **Risk assessment** (`risk_assessment.py`): runs a benign baseline prompt
  set through the target model and scores responses for violence, sexual,
  self-harm, hate/unfairness content, protected-material regurgitation,
  code vulnerabilities, and susceptibility to indirect/injected prompts.
  Cheap, fast, safe to run on every infra change.

- **Model red team** (`red_team_scan.py`): adversarially generates attack
  prompts (jailbreaks, encodings, etc.) against the raw model deployment
  and scores whether it produces unsafe content per risk category.

- **Agentic red team** (`red_team_agentic.py`): runs in Foundry's cloud
  sandbox against the *deployed agent* (not just the model), covering
  agent-specific risks like prohibited tool actions, sensitive-data
  leakage through tool calls, and task adherence under adversarial
  pressure.

All three write JSON scorecards to `agent/results/`, which the
orchestrator uploads to the Terraform-provisioned storage account under
`scorecards/<timestamp>/`.

## Connecting to an external A2A agent (SCF Compliance Assessment Agent, AWS-hosted)

`agent/a2a_collaborator.py` builds a Foundry prompt agent that delegates to
the "SCF Compliance Assessment Agent" over the
[A2A protocol](https://a2a-protocol.org/latest/) -- an AWS API
Gateway-hosted agent (control lookup, framework mapping, gap analysis,
maturity assessment, evidence checklists, questionnaire answers) with two
auth paths per its published card:

- `POST /cognito/rpc` — OAuth2 via Amazon Cognito (client_credentials or
  authorization_code)
- `POST /entra/rpc` — generic OpenID Connect against Microsoft's
  multi-tenant `common` endpoint

We're using `/entra/rpc` so the Foundry project's own managed identity can
call it without provisioning separate Cognito credentials.

This path is **confirmed working** (verified 2026-09-11). The setup below
records the exact values and the two non-obvious gotchas that make or break
it. The whole thing is codified in `infra/` — no manual portal steps needed
for a fresh deploy.

**The identity chain, end to end:**

1. **The audience app registration** (`api://43351acf-7a44-4dda-9ee9-500e76b4e9ba`,
   client id `43351acf-…`) is the SCF agent's Entra API app, owned in this
   tenant (`7cf5e1a0-…`). It has `requestedAccessTokenVersion = 2`, so it
   issues **v2.0** tokens (issuer `https://login.microsoftonline.com/7cf5e1a0-…/v2.0`,
   no issuer override needed).
2. **App-only callers need an App role, not a delegated scope.** The Foundry
   project's system-assigned managed identity calls with the client_credentials
   (app-only) flow, so the audience app must expose an **App role** with
   `allowedMemberTypes = ["Application"]` (`Agent.Invoke` here), and the MI must
   be **assigned** that role. Both are now managed by
   `infra/a2a_connection.tf` (`azuread_application_app_role.scf_agent_invoke` +
   `azuread_app_role_assignment.foundry_mi_scf_invoke`). Without this you get a
   `consent_required` / no-`roles`-claim dead end.
3. **The connection** (`infra/a2a_connection.tf`) is `ProjectManagedIdentity`
   auth with `audience = api://43351acf-…`, so the MI requests a token *for*
   that resource. `target` is `/entra/rpc`.
4. **The A2A tool** (`agent/a2a_collaborator.py`) points `agent_card_path` at
   the **route-specific** card `…/entra/.well-known/agent-card.json`, NOT the
   generic `/.well-known/agent-card.json`. The generic (and the cognito) card
   advertise `url = /cognito/rpc`; only the entra card's `url` is `/entra/rpc`.
   `A2APreviewTool` sends `message/send` to whatever `url` the resolved card
   advertises, so resolving the wrong card silently routes the Entra token to
   the Cognito route (401).

**The gotcha that costs you the last 401 — the two audience forms.** The
connection requests a token *for* `api://43351acf-…`, but Entra stamps a
**v2.0 app-only token's `aud` as the bare client-id GUID** (`43351acf-…`),
not the `api://` URI. So the AWS API Gateway JWT authorizer must be
configured to accept the **GUID** form:

```hcl
# SCF-Agent-with-A2A/terraform/terraform.tfvars
entra_tenant_id       = "7cf5e1a0-b8fd-43ef-a146-32f4b47d6e4e"
entra_audience        = "43351acf-7a44-4dda-9ee9-500e76b4e9ba"   # bare GUID: what the v2.0 aud actually is
entra_issuer_override = ""                                       # empty -> v2.0 issuer
```

Read the API Gateway access log group `/aws/apigateway/scf-agent-a2a` to
diagnose 401s — its `authError` field says exactly which check failed
(`issuer does not match`, `the token does not have a valid audience`, or
`missing: token not provided`). Setting `entra_tenant_id` also makes the SCF
Terraform create the `/entra/rpc` route, its authorizer, **and** the
`GET /entra/.well-known/agent-card.json` route as a matched set — wiring
Entra by hand in the console adds the rpc route but not the card route, which
404s card resolution.

**On this repo's side**, set in `infra/terraform.tfvars`:

```hcl
aws_a2a_audience        = "api://43351acf-7a44-4dda-9ee9-500e76b4e9ba"  # the token resource
scf_agent_app_client_id = "43351acf-7a44-4dda-9ee9-500e76b4e9ba"        # for the app-role grant
manage_scf_app_role     = true                                          # you own the app reg in this tenant
scf_agent_app_object_id = "3d347493-86db-4a92-a77c-b3a3f6798467"        # the APPLICATION object id
```

**Test it**: `python agent/a2a_collaborator.py` creates a small collaborator
agent with the A2A tool attached and forces a tool call (try "Look up SCF
control IAC-15") so you can confirm the round trip works. A successful run
shows `POST /entra/rpc -> 200` with `authError: "-"` in the API Gateway log.

**Wire it into the real pipeline**: once verified, add the same
`A2APreviewTool` to the agent `orchestrator.py` deploys, or to a dedicated
agent the orchestrator calls, so scan runs can pull compliance-framework
context from the SCF agent.

Note: `message/send` on this agent is non-blocking — it returns a Task in
`submitted` state and you poll `tasks/get` until it's terminal. Foundry's
A2A tool handles this polling internally; if you ever call the endpoint
directly (bypassing the tool) you'll need to poll yourself.

**Security posture**: a MAESTRO-framework threat model and gap analysis of
both A2A paths (outbound to the SCF agent, inbound to our target agent) is in
[docs/a2a-threat-model.md](docs/a2a-threat-model.md) — covered controls,
current gaps, and prioritized remediation.

A2A support in Foundry is in public preview — expect API shape changes;
recheck `azure-ai-projects` release notes if `A2APreviewTool` fails to
import.

## Known gaps to design around

- **Agent-to-project attachment isn't fully covered by Terraform/ARM yet**
  — model deployments land at the Foundry *account* level, and wiring a
  specific agent into a specific project reliably requires the SDK. That's
  why `orchestrator.py` creates/verifies the agent at runtime instead of
  in Terraform.
- **Some newer Foundry features (workspace connections, certain network
  isolation options) aren't in `azurerm` yet** — add the `azapi` provider
  as a fallback if you need them; it's already declared in
  `infra/providers.tf`.
- **Both the Red Teaming Agent and the agentic-scan cloud API are in
  preview.** Re-check field/method names in `red_team_scan.py` and
  `red_team_agentic.py` against current Microsoft Learn docs before
  treating this as a stable production dependency — preview APIs move.
- **RBAC role display names are mid-rename** (`Azure AI User` →
  `Foundry User`, etc.) — functionally identical, but don't be surprised
  if you see either name in the portal or provider docs.
- **Python 3.10–3.13 required** for the `redteam` extra (PyRIT drops 3.9).

## Tuning the scan

- Start with a narrow `RISK_CATEGORIES` / `ATTACK_STRATEGIES` list in
  `red_team_scan.py` (already set conservatively) and widen once you've
  measured run time and token cost — red teaming is comparatively
  expensive versus the risk-assessment pass.
- `num_objectives` controls how many attack prompts get generated per risk
  category; it's the main lever on scan depth vs. cost.
- Treat the weekly cron as a regression gate, and add `workflow_dispatch`
  runs (already wired) before shipping a new system prompt, tool, or model
  version for the agent under test.
