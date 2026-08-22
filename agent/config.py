"""
Central config. All values are populated from environment variables so the
same code runs locally (via .env) and in CI (via GitHub Actions secrets /
OIDC + the values exported from `terraform output`).
"""
import json
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

    # What's under test -- see targets.py. Only the fields for the selected
    # target_kind are actually required; the others are left unset.
    target_kind: str               # TARGET_KIND: "azure_deployment" (default) or "http"
    target_deployment: str | None          # required when target_kind == "azure_deployment"
    target_http_url: str | None            # required when target_kind == "http"
    target_http_method: str
    target_http_headers: dict | None
    target_http_response_path: str

    adversarial_deployment: str | None  # generates attack prompts -- currently unused by
                                         # the installed RedTeam SDK, see red_team_scan.py
    agent_name: str | None         # name of the deployed Foundry Agent under test (agentic scans)
    results_storage_account: str
    results_container: str = "scorecards"


def load_settings() -> Settings:
    target_kind = os.environ.get("TARGET_KIND", "azure_deployment")

    target_deployment = None
    target_http_url = None
    target_http_headers = None
    if target_kind == "azure_deployment":
        target_deployment = _require("TARGET_MODEL_DEPLOYMENT")
    elif target_kind == "http":
        target_http_url = _require("TARGET_HTTP_URL")
        raw_headers = os.environ.get("TARGET_HTTP_HEADERS")
        if raw_headers:
            target_http_headers = json.loads(raw_headers)
    else:
        raise RuntimeError(
            f"Unknown TARGET_KIND: {target_kind!r} (expected 'azure_deployment' or 'http')"
        )

    return Settings(
        subscription_id=_require("AZURE_SUBSCRIPTION_ID"),
        resource_group=_require("AZURE_RESOURCE_GROUP"),
        project_endpoint=_require("AZURE_AI_PROJECT_ENDPOINT"),
        target_kind=target_kind,
        target_deployment=target_deployment,
        target_http_url=target_http_url,
        target_http_method=os.environ.get("TARGET_HTTP_METHOD", "POST"),
        target_http_headers=target_http_headers,
        target_http_response_path=os.environ.get("TARGET_HTTP_RESPONSE_PATH", "response"),
        adversarial_deployment=os.environ.get("ADVERSARIAL_MODEL_DEPLOYMENT"),
        agent_name=os.environ.get("AZURE_AI_AGENT_NAME"),  # optional: only needed for agentic scan
        results_storage_account=_require("RESULTS_STORAGE_ACCOUNT"),
    )
