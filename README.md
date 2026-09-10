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

**The one value the card doesn't publish**: because the `entra` scheme is
generic `openIdConnect` (not a scoped `oauth2` flow like the `cognito`
scheme), the card has no `audience`/`scopes` field for it — that
validation lives only in the AWS Lambda authorizer's own config. You need
to get the expected `aud` from that authorizer's code/environment before
the connection will authenticate successfully.

1. **Get the audience** the `/entra/rpc` Lambda authorizer validates
   (check its code/env vars for `aud`, `audience`, or JWKS validation
   logic).
2. **Confirm a service principal for that audience exists in your
   Foundry project's tenant** — if the App Registration behind that
   audience lives in a different tenant, it needs to be multi-tenant and
   consented into yours, or the Lambda needs to accept your tenant's
   issuer specifically.
3. **Create the connection** — either run `agent/setup_a2a_connection.sh`
   (target already defaults to the confirmed
   `https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc`;
   just set `AWS_A2A_AUDIENCE`), or apply `infra/a2a_connection.tf` by
   setting `aws_a2a_audience` in `terraform.tfvars`. Pick one, not both.
4. **Test it**: `python agent/a2a_collaborator.py` creates a small
   collaborator agent with the A2A tool attached and forces a tool call
   (try a prompt like "Look up SCF control IAC-15" — one of the card's own
   example skills) so you can confirm the round trip works.
5. **Wire it into the real pipeline**: once verified, add the same
   `A2APreviewTool` to the agent `orchestrator.py` deploys, or to a
   dedicated agent the orchestrator calls, so scan runs can pull
   compliance-framework context from the SCF agent.

Note: `message/send` on this agent is non-blocking — it returns a Task in
`submitted` state and you poll `tasks/get` until it's terminal. Foundry's
A2A tool handles this polling internally; if you ever call the endpoint
directly (bypassing the tool) you'll need to poll yourself.

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
