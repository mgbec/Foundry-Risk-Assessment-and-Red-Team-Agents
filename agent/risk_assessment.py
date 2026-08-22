"""
Risk Assessment Agent
======================
Non-adversarial safety scoring: runs a target model/app against a baseline
prompt set and scores the responses with Azure AI Foundry's built-in safety
evaluators (content risk + a few responsible-AI checks). This is meant to
run BEFORE the Red Teaming Agent (red_team_scan.py) as a cheap, fast gate --
red teaming is comparatively expensive and adversarial, this is your
day-to-day regression check.

Docs: Azure AI Evaluation SDK -> "Risk and safety evaluators"
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from azure.identity import DefaultAzureCredential
from azure.ai.evaluation import (
    evaluate,
    ViolenceEvaluator,
    SexualEvaluator,
    SelfHarmEvaluator,
    HateUnfairnessEvaluator,
    ProtectedMaterialEvaluator,
    CodeVulnerabilityEvaluator,
    IndirectAttackEvaluator,
)

from config import load_settings

BASELINE_PROMPTS_PATH = Path(__file__).parent / "data" / "baseline_prompts.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


def build_dataset(call_target: Callable[[str], str]) -> Path:
    """Runs the target once per baseline prompt and writes a query/response
    jsonl file in the shape azure-ai-evaluation's `evaluate()` expects."""
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / "baseline_query_response.jsonl"

    with BASELINE_PROMPTS_PATH.open() as f_in, out_path.open("w") as f_out:
        for line in f_in:
            record = json.loads(line)
            query = record["query"]
            response = call_target(query)
            f_out.write(json.dumps({"query": query, "response": response}) + "\n")

    return out_path


def run_risk_assessment(call_target: Callable[[str], str]) -> dict:
    settings = load_settings()
    credential = DefaultAzureCredential()

    azure_ai_project = settings.project_endpoint  # accepts the project endpoint URL directly

    evaluators = {
        "violence": ViolenceEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "sexual": SexualEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "self_harm": SelfHarmEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "hate_unfairness": HateUnfairnessEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "protected_material": ProtectedMaterialEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "code_vulnerability": CodeVulnerabilityEvaluator(credential=credential, azure_ai_project=azure_ai_project),
        "indirect_attack": IndirectAttackEvaluator(credential=credential, azure_ai_project=azure_ai_project),
    }

    dataset_path = build_dataset(call_target)

    result = evaluate(
        data=str(dataset_path),
        evaluators=evaluators,
        azure_ai_project=azure_ai_project,
        output_path=str(RESULTS_DIR / "risk_assessment_results.json"),
    )

    return result


if __name__ == "__main__":
    import argparse

    from targets import add_target_cli_args, target_from_args

    parser = argparse.ArgumentParser(description=__doc__)
    add_target_cli_args(parser)
    args = parser.parse_args()

    settings = load_settings()
    call_target = target_from_args(args, settings)

    scorecard = run_risk_assessment(call_target)
    print(json.dumps(scorecard, indent=2)[:2000])
