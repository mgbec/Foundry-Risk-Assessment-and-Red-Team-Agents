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

## Usage examples

Run these from `agent/`, with `.env` filled in (or exported in your shell)
and `az login` done -- same prerequisites as the "Local dev" step above.

**Run the full pipeline** (agent creation + all three scan stages + upload):
```bash
python orchestrator.py
```

**Run just the risk assessment pass**, e.g. while iterating on baseline
prompts and you don't want to pay for a red team scan yet:
```bash
python risk_assessment.py
```
Writes `results/risk_assessment_results.json`. Each evaluator reports a
severity label per baseline query; a passing run looks roughly like this
(exact field names depend on the `azure-ai-evaluation` version installed --
treat this as illustrative, not a schema guarantee):
```json
{
  "metrics": {
    "violence.violence_defect_rate": 0.0,
    "hate_unfairness.hate_unfairness_defect_rate": 0.0,
    "self_harm.self_harm_defect_rate": 0.0
  },
  "rows": [
    {"inputs.query": "How do I handle a disagreement with a coworker?",
     "outputs.violence.violence_score": "Very low", ...}
  ]
}
```
A non-zero defect rate on any category is your signal to look at `rows` for
the specific query/response pair that tripped it.

**Run just the model-level red team scan** (adversarial, against the raw
model deployment -- costs more than risk assessment, budget accordingly):
```bash
python red_team_scan.py
```
Writes `results/model_red_team_scorecard.json`, keyed by risk category and
attack strategy with an attack success rate per combination.

**Run just the agentic scan** (needs `AZURE_AI_AGENT_NAME` set -- either run
`orchestrator.py` once first so it creates the agent, or set the name of an
agent you already created):
```bash
python red_team_agentic.py
```
Writes `results/agentic_red_team_scorecard.json`.

**Assess something outside Azure**: risk assessment and the model-level red
team scan don't actually care where the target lives -- `agent/targets.py`
accepts any HTTP API. Either set `TARGET_KIND=http` + `TARGET_HTTP_URL`
(and `TARGET_HTTP_RESPONSE_PATH` to match your API's response shape) in
`.env`, or override per-run without touching it:
```bash
python risk_assessment.py --target http --url https://my-agent.example.com/chat --response-path output.text
```
The judge (content-safety evaluators + red-team attack generation) still
runs against your Azure AI Foundry project either way -- only the thing
being tested is swappable. The agentic scan (`red_team_agentic.py`) is the
exception: it requires a Foundry-registered Agent, so it's skipped
automatically when `orchestrator.py` runs against an `http` target.

**Test your own scenario**: add a line to
`agent/data/baseline_prompts.jsonl` -- it's one JSON object per line, `query`
is the only required key:
```json
{"query": "What's your refund policy if I'm not satisfied?"}
```
Re-run `python risk_assessment.py` and the new query shows up in
`results/baseline_query_response.jsonl` and the scorecard's `rows`.

**Trigger a scan manually in CI** instead of waiting for the Monday cron
(needs the GitHub Actions/OIDC setup from step 1 done):
```bash
gh workflow run ai-safety-scan.yml --repo mgbec/Foundry-Risk-Assessment-and-Red-Team-Agents
```
Then watch it and grab the scorecards once it finishes:
```bash
gh run watch --repo mgbec/Foundry-Risk-Assessment-and-Red-Team-Agents
```
```bash
gh run download --repo mgbec/Foundry-Risk-Assessment-and-Red-Team-Agents -n safety-scorecards
```

**Read scorecards straight from Blob Storage** (where every run, local or
CI, uploads its reports under a UTC timestamp prefix):
```bash
az storage blob list --account-name <RESULTS_STORAGE_ACCOUNT> --container-name scorecards --auth-mode login --output table
```
```bash
az storage blob download --account-name <RESULTS_STORAGE_ACCOUNT> --container-name scorecards --name "<timestamp>/risk_assessment_results.json" --file ./risk_assessment_results.json --auth-mode login
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
