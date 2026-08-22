"""
Target abstraction: the thing under test.

A target is just `Callable[[str], str]` -- takes a query, returns the
model/agent's response text. risk_assessment.py and red_team_scan.py only
ever call it that way, so any implementation works: an Azure Foundry model
deployment, or any external HTTP API (your own agent, a non-Azure model
provider, anything that answers a question over the network).

The *judge* is a separate concern and is NOT pluggable here: the
content-safety evaluators (risk_assessment.py) and the red-team attack
generator (red_team_scan.py) always score against your Azure AI Foundry
project, regardless of what target they're pointed at.

red_team_agentic.py is a different story -- it calls the Foundry cloud
scan API with `target={"type": "agent", ...}`, which requires a
Foundry-registered Agent resource. That path isn't covered by this module;
an externally hosted agent would need to be proxied into Foundry to use it.
"""
from __future__ import annotations

import argparse
from typing import Callable

import requests
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

Target = Callable[[str], str]


def azure_deployment_target(project_endpoint: str, deployment: str) -> Target:
    """Target = a model deployment on your Azure AI Foundry account."""
    client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())

    def _call(query: str) -> str:
        response = client.inference.get_chat_completions_client().complete(
            model=deployment,
            messages=[{"role": "user", "content": query}],
        )
        return response.choices[0].message.content

    return _call


def _extract(data, path: str):
    """Walk a dotted path (e.g. 'choices.0.message.content') into a parsed JSON body."""
    current = data
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def http_endpoint_target(
    url: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    query_field: str = "query",
    response_path: str = "response",
    timeout: float = 60.0,
) -> Target:
    """
    Target = any external HTTP API. Not Azure-specific at all -- this is
    how you point the pipeline at a non-Azure agent or model.

    Sends `{query_field: query}` as the JSON body and pulls the reply text
    out of the JSON response at `response_path` (e.g.
    "choices.0.message.content" for an OpenAI-shaped response, "output.text"
    for a custom API). Adjust `query_field`/`response_path` to match
    whatever shape your endpoint actually speaks.
    """
    headers = headers or {}

    def _call(query: str) -> str:
        resp = requests.request(method, url, json={query_field: query}, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return str(_extract(resp.json(), response_path))

    return _call


def build_target_from_settings(settings) -> Target:
    """Construct the target Settings was configured for (TARGET_KIND env var)."""
    if settings.target_kind == "azure_deployment":
        return azure_deployment_target(settings.project_endpoint, settings.target_deployment)
    if settings.target_kind == "http":
        return http_endpoint_target(
            settings.target_http_url,
            method=settings.target_http_method,
            headers=settings.target_http_headers,
            response_path=settings.target_http_response_path,
        )
    raise ValueError(f"Unknown target kind: {settings.target_kind!r}")


def add_target_cli_args(parser: argparse.ArgumentParser) -> None:
    """Adds --target/--deployment/--url/... overrides to a script's argparser.

    Leaving all of these unset falls back to Settings (env vars / .env) --
    the CLI is for a one-off override without editing config.
    """
    parser.add_argument("--target", choices=["azure", "http"], default=None,
                         help="Override TARGET_KIND for this run.")
    parser.add_argument("--deployment", default=None,
                         help="Override TARGET_MODEL_DEPLOYMENT (azure target).")
    parser.add_argument("--url", default=None,
                         help="Override TARGET_HTTP_URL (http target).")
    parser.add_argument("--method", default=None,
                         help="HTTP method for the http target (default POST).")
    parser.add_argument("--header", action="append", default=None, metavar="KEY:VALUE",
                         help="HTTP header for the http target, repeatable, e.g. "
                              "--header 'Authorization:Bearer sk-...'")
    parser.add_argument("--response-path", default=None,
                         help="Dotted path into the JSON response for the http target, "
                              "e.g. 'choices.0.message.content'.")


def target_from_args(args: argparse.Namespace, settings) -> Target:
    """Builds a target from parsed CLI args, falling back to `settings` for anything unset."""
    kind = args.target or settings.target_kind

    if kind == "azure":
        deployment = args.deployment or settings.target_deployment
        if not deployment:
            raise ValueError("No model deployment given -- pass --deployment or set TARGET_MODEL_DEPLOYMENT.")
        return azure_deployment_target(settings.project_endpoint, deployment)

    if kind == "http":
        url = args.url or settings.target_http_url
        if not url:
            raise ValueError("No URL given -- pass --url or set TARGET_HTTP_URL.")
        headers = dict(settings.target_http_headers or {})
        for h in args.header or []:
            key, _, value = h.partition(":")
            headers[key.strip()] = value.strip()
        return http_endpoint_target(
            url,
            method=args.method or settings.target_http_method,
            headers=headers,
            response_path=args.response_path or settings.target_http_response_path,
        )

    raise ValueError(f"Unknown target kind: {kind!r} (expected 'azure' or 'http')")
