"""
Red Teaming Agent -- agentic level
====================================
Runs the AI Red Teaming Agent IN THE CLOUD (Foundry-hosted) against a
deployed Foundry Agent, rather than a raw model. This is what you want once
your orchestrator/tool-using agent exists, since it covers agent-specific
risk categories (e.g. prohibited actions, tool misuse, sensitive-data
exposure via tool calls) in a sandboxed environment instead of your own
runner.

Prereqs:
  - AZURE_AI_AGENT_NAME must point at an agent already deployed in the
    Foundry project (create it via orchestrator.py or the Foundry portal
    first -- this is a known gap in Terraform/ARM coverage today).
  - Caller needs "Foundry User" on the project (see infra/main.tf RBAC).
  - `az login` locally, or federated OIDC identity in CI -- keyless auth
    via DefaultAzureCredential is the supported/recommended path.

Because this API is in preview, verify field/method names against current
docs before relying on this in production:
https://learn.microsoft.com/azure/foundry/how-to/develop/run-ai-red-teaming-cloud
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

from config import load_settings

RESULTS_DIR = Path(__file__).parent / "results"

AGENTIC_RISK_CATEGORIES = [
    "prohibited_actions",
    "sensitive_data_leakage",
    "task_adherence",
]


def run_agentic_red_team(poll_interval_seconds: int = 15, timeout_seconds: int = 1800) -> dict:
    settings = load_settings()
    if not settings.agent_name:
        raise RuntimeError(
            "AZURE_AI_AGENT_NAME is not set. Deploy the orchestrator/target "
            "agent first (see orchestrator.py) then set this env var."
        )

    client = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())

    red_team = client.evals.create(
        display_name="scheduled-agentic-red-team",
        target={
            "type": "agent",
            "agent_name": settings.agent_name,
            "deployment_name": settings.target_deployment,
        },
        risk_categories=AGENTIC_RISK_CATEGORIES,
    )

    print(f"[RedTeam] Started run id={red_team.id}, polling for completion...")

    elapsed = 0
    while elapsed < timeout_seconds:
        current = client.evals.retrieve(red_team.id)
        status = getattr(current, "status", "unknown")
        if status in ("completed", "failed", "canceled"):
            break
        time.sleep(poll_interval_seconds)
        elapsed += poll_interval_seconds
    else:
        raise TimeoutError(f"Red team run {red_team.id} did not finish within {timeout_seconds}s")

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / "agentic_red_team_scorecard.json"
    out_path.write_text(json.dumps(current.as_dict() if hasattr(current, "as_dict") else vars(current), indent=2, default=str))

    print(f"[RedTeam] Run {red_team.id} finished with status={status}. Scorecard: {out_path}")
    return current


if __name__ == "__main__":
    run_agentic_red_team()
