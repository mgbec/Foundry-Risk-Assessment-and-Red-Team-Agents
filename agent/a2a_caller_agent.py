"""
A2A caller agent (Foundry-native)
====================================
Calls a2a_target_agent.py's agent using Foundry's own first-party A2A tool
(A2APreviewTool) instead of the third-party a2a-sdk used by
a2a_client_example.py -- to isolate whether the empty-content finding
documented there is a Foundry incoming-A2A bug, or specific to how the
external a2a-sdk client parses Foundry's response.

CONFIRMED (tested 2026-08-24): both of Microsoft's own documented paths
for a Foundry agent calling another Foundry agent over A2A are broken.
Leaving agent_card_path unset (the documented default -- "Foundry resolves
it automatically") 404s on the card fetch. Setting it explicitly to
"agentCard/v1.0" (the exact path a2a_client_example.py independently
proved correct against this same endpoint -- the documented escape hatch)
gets rejected outright: "Agent card path is invalid for a Foundry agent.
Either fix the agent card path or remove it to use the default agent card
path." Both options the error message itself offers fail. Combined with
a2a_client_example.py's task-completes-with-no-content finding, that's
three independently-tested invocation paths, three different failures,
against Microsoft's own first-party tooling. See the README's "A2A
(agent-to-agent) access" section -- this looks like a genuine current
preview-service gap worth filing with Microsoft directly, not something to
keep working around here.

This is Microsoft's documented "recommended" pattern for one Foundry agent
calling another: an A2A project connection + the A2APreviewTool, attached
to a caller agent. (A "toolbox" is an optional extra layer on top of the
same connection/tool for reusing it across multiple agents -- not required
just to test this once.)

--target-url lets this same script instead call a genuinely external,
non-Foundry A2A server (e.g. a2a-test-server/, a minimal standards-
compliant echo agent deployed to Azure Container Apps specifically to
test this without the Foundry-target special-casing that broke above).
External targets use unauthenticated access and skip the Foundry-specific
agent_card_path override, since a compliant server publishes its card at
the normal A2A-spec default (.well-known/agent-card.json).

Prerequisites this script assumes are already done:
  - For the default (Foundry target): a2a_target_agent.py has been run at
    least once, and infra/main.tf's Foundry Agent Consumer role assignment
    has been applied.
  - For --target-url: the external server is deployed and reachable.

Usage:
    python a2a_caller_agent.py
    python a2a_caller_agent.py --target-agent-name my-other-agent
    python a2a_caller_agent.py --target-url https://a2a-test-server.<region>.azurecontainerapps.io --message hello
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


def ensure_a2a_connection(settings, target_a2a_url: str, connection_name: str, external: bool) -> None:
    """Creates (or updates) a project connection pointing at the target's
    A2A endpoint. The Python SDK doesn't expose connection creation yet,
    so this goes through the ARM REST API directly (same call shape
    Microsoft's own docs use).

    external=True: a genuinely non-Foundry target, unauthenticated (this
    test server has nothing worth protecting -- see a2a-test-server/app.py).
    external=False: another Foundry agent, authenticated via this
    project's own identity -- no stored secret either way.
    """
    account, project = _parse_endpoint(settings.project_endpoint)

    arm_token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com/subscriptions/{settings.subscription_id}"
        f"/resourceGroups/{settings.resource_group}/providers/Microsoft.CognitiveServices"
        f"/accounts/{account}/projects/{project}/connections/{connection_name}"
        f"?api-version=2025-04-01-preview"
    )
    properties = {
        "group": "ServicesAndApps",
        "category": "RemoteA2A",
        "expiryTime": None,
        "target": target_a2a_url,
        "isSharedToAll": True,
        "sharedUserList": [],
        "Credentials": {},
        "metadata": {"ApiType": "Azure"},
    }
    if external:
        properties["authType"] = "None"
    else:
        properties["authType"] = "AgenticIdentityToken"
        properties["audience"] = "https://ai.azure.com"

    body = {
        "tags": None,
        "location": None,
        "name": connection_name,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": properties,
    }
    resp = requests.put(url, headers={"Authorization": f"Bearer {arm_token}"}, json=body, timeout=60)
    resp.raise_for_status()
    print(f"[A2ACaller] Connection '{connection_name}' -> {target_a2a_url}")


def ensure_caller_agent(settings, project: AIProjectClient, connection_name: str, agent_name: str, external: bool):
    a2a_connection = project.connections.get(connection_name)

    if external:
        # A compliant external server publishes its card at the normal
        # A2A-spec default (.well-known/agent-card.json) -- no Foundry-
        # specific override needed, this is the plain/simple path.
        tool = A2APreviewTool(project_connection_id=a2a_connection.id)
    else:
        # Docs say "don't set an agent card path, Foundry resolves it
        # automatically" for a Foundry-to-Foundry target -- that auto-
        # detection doesn't actually work (still 404s with just send_
        # credentials_for_agent_card), likely defaulting to the A2A-spec
        # standard path instead of Foundry's nonstandard agentCard/v1.0.
        # a2a_client_example.py already proved agentCard/v1.0 is the right
        # path for this same endpoint, so set it explicitly -- though this
        # itself gets rejected too (see the CONFIRMED note up top).
        tool = A2APreviewTool(
            project_connection_id=a2a_connection.id,
            send_credentials_for_agent_card=True,
            agent_card_path="agentCard/v1.0",
        )

    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=settings.target_deployment,
            instructions=(
                "You are a helpful assistant. For any request, delegate to "
                "the connected agent -- don't try to answer it yourself."
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
    parser.add_argument("--target-agent-name", default=DEFAULT_TARGET_AGENT_NAME,
                         help="Name of the Foundry target agent (ignored if --target-url is set).")
    parser.add_argument("--target-url", default=None,
                         help="A2A base URL of a genuinely external, non-Foundry server to call instead.")
    parser.add_argument("--message", default="What's the balance on account ACC-1001?")
    args = parser.parse_args()

    external = args.target_url is not None
    target_a2a_url = args.target_url
    connection_name = CONNECTION_NAME
    agent_name = CALLER_AGENT_NAME
    if external:
        connection_name = "a2a-external-test-conn"
        agent_name = "a2a-caller-agent-external"

    settings = load_settings()
    with trace_run("a2a-caller-agent-run"):
        if not external:
            target_a2a_url = f"{settings.project_endpoint}/agents/{args.target_agent_name}/endpoint/protocols/a2a"

        ensure_a2a_connection(settings, target_a2a_url, connection_name, external)

        project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        agent = ensure_caller_agent(settings, project, connection_name, agent_name, external)

        print(f"[A2ACaller] Sending: {args.message!r}")
        reply = run_test(project, agent, args.message)
        print(f"[A2ACaller] Reply: {reply!r}")
