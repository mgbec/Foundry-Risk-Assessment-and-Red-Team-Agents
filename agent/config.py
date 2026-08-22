"""
Central config. All values are populated from environment variables so the
same code runs locally (via .env) and in CI (via GitHub Actions secrets /
OIDC + the values exported from `terraform output`).
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Populate it from `terraform output` (see infra/outputs.tf)."
        )
    return val


@dataclass(frozen=True)
class Settings:
    subscription_id: str
    resource_group: str
    project_endpoint: str          # AZURE_AI_PROJECT_ENDPOINT, e.g. https://<account>.services.ai.azure.com/api/projects/<project>
    target_deployment: str         # model under test
    adversarial_deployment: str    # generates attack prompts
    agent_name: str | None         # name of the deployed Foundry Agent under test (agentic scans)
    results_storage_account: str
    results_container: str = "scorecards"


def load_settings() -> Settings:
    return Settings(
        subscription_id=_require("AZURE_SUBSCRIPTION_ID"),
        resource_group=_require("AZURE_RESOURCE_GROUP"),
        project_endpoint=_require("AZURE_AI_PROJECT_ENDPOINT"),
        target_deployment=_require("TARGET_MODEL_DEPLOYMENT"),
        adversarial_deployment=_require("ADVERSARIAL_MODEL_DEPLOYMENT"),
        agent_name=os.environ.get("AZURE_AI_AGENT_NAME"),  # optional: only needed for agentic scan
        results_storage_account=_require("RESULTS_STORAGE_ACCOUNT"),
    )
