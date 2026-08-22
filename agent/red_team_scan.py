"""
Red Teaming Agent -- model level
==================================
Adversarially probes the TARGET MODEL deployment using Microsoft's AI Red
Teaming Agent (PyRIT integration in azure-ai-evaluation[redteam]).

This runs as a "local" scan: it executes on whatever machine/runner invokes
this script, but generates attack prompts and scores results using your
Foundry project (adversarial model + Azure AI content-safety backend).

For scanning a deployed AGENT (with tools / multi-turn / agentic risks like
prohibited actions), use red_team_agentic.py instead, which runs in the
cloud against a named Foundry Agent.

Requires Python 3.10-3.13.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.ai.evaluation.red_team import RedTeam, RiskCategory, AttackStrategy
from azure.ai.projects import AIProjectClient

from config import load_settings

RESULTS_DIR = Path(__file__).parent / "results"

# Start conservative; widen once the pipeline is stable and you've sized
# out the cost/time budget. Full category list also includes
# ExplicitProhibitedContent / others depending on SDK version -- check
# `RiskCategory` in your installed package.
RISK_CATEGORIES = [
    RiskCategory.Violence,
    RiskCategory.HateUnfairness,
    RiskCategory.Sexual,
    RiskCategory.SelfHarm,
]

# Attack complexity to exercise. Start with baseline + a couple of
# transformation strategies (e.g. encoding, role-play framing) and expand.
ATTACK_STRATEGIES = [
    AttackStrategy.Baseline,
    AttackStrategy.Jailbreak,
]


async def run_model_red_team() -> dict:
    settings = load_settings()
    credential = DefaultAzureCredential()
    client = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)

    def target_fn(query: str) -> str:
        response = client.inference.get_chat_completions_client().complete(
            model=settings.target_deployment,
            messages=[{"role": "user", "content": query}],
        )
        return response.choices[0].message.content

    red_team_agent = RedTeam(
        azure_ai_project=settings.project_endpoint,
        credential=credential,
        risk_categories=RISK_CATEGORIES,
        num_objectives=10,  # attack objectives generated per risk category
    )

    RESULTS_DIR.mkdir(exist_ok=True)
    scan_result = await red_team_agent.scan(
        target=target_fn,
        attack_strategies=ATTACK_STRATEGIES,
        output_path=str(RESULTS_DIR / "model_red_team_scorecard.json"),
    )

    return scan_result


if __name__ == "__main__":
    result = asyncio.run(run_model_red_team())
    print(json.dumps(getattr(result, "scorecard", result), indent=2, default=str)[:2000])
