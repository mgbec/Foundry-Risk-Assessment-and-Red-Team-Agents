"""
A2A caller agent (Foundry-native)
====================================
Calls a2a_target_agent.py's agent using Foundry's own first-party A2A tool
(A2APreviewTool) instead of the third-party a2a-sdk used by
a2a_client_example.py -- to isolate whether the empty-content finding
documented there is a Foundry incoming-A2A bug, or specific to how the
external a2a-sdk client parses Foundry's response.

If THIS script gets a real answer back where a2a_client_example.py didn't,
the gap is in a2a-sdk's handling, not Foundry's incoming-A2A endpoint. If
this also comes back empty, that's stronger evidence of a genuine
server-side preview limitation.

This is Microsoft's documented "recommended" pattern for one Foundry agent
calling another: an A2A project connection + the A2APreviewTool, attached
to a caller agent. (A "toolbox" is an optional extra layer on top of the
same connection/tool for reusing it across multiple agents -- not required
just to test this once.)

Prerequisites this script assumes are already done:
  - a2a_target_agent.py has been run at least once (the target agent
    exists with incoming A2A enabled).
  - infra/main.tf's Foundry Agent Consumer role assignment has been
    applied, so whichever identity ends up calling the target agent is
    authorized.

Usage:
    python a2a_caller_agent.py
    python a2a_caller_agent.py --target-agent-name my-other-agent
"""
from __future__ import annotations

import argparse
import re

import requests
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import A2APreviewTool, PromptAgentDefinition

from config import load_settings
from observability import trace_run
from a2a_target_agent import DEFAULT_AGENT_NAME as DEFAULT_TARGET_AGENT_NAME

CALLER_AGENT_NAME = "a2a-caller-agent"
CONNECTION_NAME = "a2a-target-agent-conn"

_ENDPOINT_RE = re.compile(r"https://(?P<account>[^.]+)\.services\.ai\.azure\.com/api/projects/(?P<project>[^/]+)")


def _parse_endpoint(project_endpoint: str) -> tuple[str, str]:
    match = _ENDPOINT_RE.match(project_endpoint)
    if not match:
        raise ValueError(f"Couldn't parse account/project out of {project_endpoint!r}")
    return match.group("account"), match.group("project")


def ensure_a2a_connection(settings, target_agent_name: str) -> None:
    """Creates (or updates) a project connection pointing at the target
    agent's A2A endpoint, authenticated via this project's own identity --
    no stored secret. The Python SDK doesn't expose connection creation
    yet, so this goes through the ARM REST API directly (same call shape
    Microsoft's own docs use)."""
    account, project = _parse_endpoint(settings.project_endpoint)
    target_a2a_url = f"{settings.project_endpoint}/agents/{target_agent_name}/endpoint/protocols/a2a"

    arm_token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com/subscriptions/{settings.subscription_id}"
        f"/resourceGroups/{settings.resource_group}/providers/Microsoft.CognitiveServices"
        f"/accounts/{account}/projects/{project}/connections/{CONNECTION_NAME}"
        f"?api-version=2025-04-01-preview"
    )
    body = {
        "tags": None,
        "location": None,
        "name": CONNECTION_NAME,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": "AgenticIdentityToken",
            "group": "ServicesAndApps",
            "category": "RemoteA2A",
            "expiryTime": None,
            "target": target_a2a_url,
            "isSharedToAll": True,
            "sharedUserList": [],
            "audience": "https://ai.azure.com",
            "Credentials": {},
            "metadata": {"ApiType": "Azure"},
        },
    }
    resp = requests.put(url, headers={"Authorization": f"Bearer {arm_token}"}, json=body, timeout=60)
    resp.raise_for_status()
    print(f"[A2ACaller] Connection '{CONNECTION_NAME}' -> {target_a2a_url}")


def ensure_caller_agent(settings, project: AIProjectClient):
    a2a_connection = project.connections.get(CONNECTION_NAME)
    # Docs say "don't set an agent card path, Foundry resolves it
    # automatically" for a Foundry-to-Foundry target -- that auto-detection
    # appears not to actually work (still 404s with send_credentials_for_
    # agent_card alone), likely defaulting to the A2A-spec standard
    # .well-known/agent-card.json instead of Foundry's nonstandard
    # agentCard/v1.0 path. a2a_client_example.py already proved
    # agentCard/v1.0 is the right path for this same endpoint, so set it
    # explicitly rather than trust the auto-detection.
    tool = A2APreviewTool(
        project_connection_id=a2a_connection.id,
        send_credentials_for_agent_card=True,
        agent_card_path="agentCard/v1.0",
    )

    agent = project.agents.create_version(
        agent_name=CALLER_AGENT_NAME,
        definition=PromptAgentDefinition(
            model=settings.target_deployment,
            instructions=(
                "You are a helpful assistant. For any question about a customer "
                "account (balance, password reset, etc.), delegate to the "
                "connected agent -- don't try to answer it yourself."
            ),
            tools=[tool],
        ),
    )
    print(f"[A2ACaller] '{agent.name}' version {agent.version} ready, connection id={a2a_connection.id}")
    return agent


def run_test(project: AIProjectClient, agent, message: str) -> str:
    openai = project.get_openai_client()
    response = openai.responses.create(
        tool_choice="required",
        input=message,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )
    return response.output_text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-agent-name", default=DEFAULT_TARGET_AGENT_NAME)
    parser.add_argument("--message", default="What's the balance on account ACC-1001?")
    args = parser.parse_args()

    settings = load_settings()
    with trace_run("a2a-caller-agent-run"):
        ensure_a2a_connection(settings, args.target_agent_name)

        project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        agent = ensure_caller_agent(settings, project)

        print(f"[A2ACaller] Sending: {args.message!r}")
        reply = run_test(project, agent, args.message)
        print(f"[A2ACaller] Reply: {reply!r}")
