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
