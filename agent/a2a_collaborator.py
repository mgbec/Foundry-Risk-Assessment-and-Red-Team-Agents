"""
A2A connection to the AWS-hosted agent
========================================
Attaches the A2A tool -- pointed at the AWS agent behind
https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/ (Entra-JWT-authenticated
/entra/rpc path) -- to a Foundry prompt agent, so that agent can call the AWS
agent mid-conversation.

Prereqs (do these first, see agent/setup_a2a_connection.sh):
  1. Confirm the exact RPC url + audience from the AWS agent's card:
     GET https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/.well-known/agent-card.json
  2. An Entra App Registration exists whose Application ID URI is the
     audience the AWS Lambda validates.
  3. Your Foundry project's managed identity (or the target agent's
     agentic identity) can obtain tokens for that audience.
  4. `./setup_a2a_connection.sh` has been run, creating the project
     connection referenced by A2A_CONNECTION_NAME below.

This is separate from orchestrator.py's target/agentic red-team agent --
use it to build a COLLABORATOR agent that can delegate to the AWS agent,
or import `attach_a2a_tool` and add the tool to an existing agent
definition.
"""
from __future__ import annotations

import os

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, A2APreviewTool

from config import load_settings

A2A_CONNECTION_NAME = os.environ.get("A2A_CONNECTION_NAME", "aws-redteam-agent-a2a")
A2A_AGENT_NAME = os.environ.get("A2A_COLLABORATOR_AGENT_NAME", "aws-agent-collaborator")


def get_a2a_tool(project: AIProjectClient) -> A2APreviewTool:
    """Looks up the pre-created connection (see setup_a2a_connection.sh)
    and returns an A2A tool definition referencing it.

    base_url is the agent's origin; agent_card_path is set EXPLICITLY to
    the exact, curl-verified card URL so Agent Service doesn't have to
    derive it by concatenation (that derivation is the suspected cause of
    the 404s seen with base_url alone). The connection's `target` stays
    pointed at /entra/rpc for actual tool-call invocation.

    IMPORTANT: use the ROUTE-SPECIFIC card (/entra/.well-known/agent-card.json),
    NOT the generic /.well-known/agent-card.json. The SCF agent serves a
    separate card per auth route (see docs/a2a-integration.md); the generic
    card's top-level `url` is the /cognito/rpc endpoint, so resolving it would
    make the A2A tool send message/send to the Cognito route -- which rejects
    our Entra token with a 401. The /entra card's `url` is /entra/rpc, matching
    the connection target and the token audience configured here.

    PREREQUISITE ON THE AWS SIDE (verified 2026-09-11): the deployed SCF
    stack currently serves the entra card route as 404 even though POST
    /entra/rpc exists and is guarded (returns 401 without a token). The
    a2a_bridge Lambda already builds a per-prefix card
    (build_card("entra")); what's missing is the API Gateway GET route
    mapping /entra/.well-known/agent-card.json -> that Lambda. Until that
    route is added on the AWS side, card resolution here 404s. A2APreviewTool
    has no way to supply the card inline or to override the RPC target
    independently of the resolved card (its only fields are
    project_connection_id / base_url / agent_card_path /
    send_credentials_for_agent_card), so the entra card MUST resolve for the
    Entra path to work. Fix on the AWS stack: add the entra GET route
    alongside the existing /cognito/.well-known/agent-card.json route (the
    Terraform in SCF-Agent-with-A2A/terraform/a2a.tf adds both when
    entra_tenant_id is set -- this deployment appears to have had Entra
    wired in the console, which added /entra/rpc + its authorizer but not
    the card GET route).
    """
    connection = project.connections.get(A2A_CONNECTION_NAME)
    origin = "https://ar4y22vewc.execute-api.us-east-1.amazonaws.com"
    return A2APreviewTool(
        project_connection_id=connection.id,
        base_url=origin,
        agent_card_path=f"{origin}/entra/.well-known/agent-card.json",
    )


def create_or_update_collaborator_agent(instructions: str | None = None) -> str:
    """Creates (or version-bumps) a prompt agent whose only job is to call
    the AWS agent through A2A. Returns the agent name."""
    settings = load_settings()
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())

    tool = get_a2a_tool(project)

    agent = project.agents.create_version(
        agent_name=A2A_AGENT_NAME,
        definition=PromptAgentDefinition(
            model=settings.target_deployment,
            instructions=instructions or (
                "You are a coordinator agent. For anything the user asks that "
                "the remote AWS agent can help with, call it through the A2A "
                "tool and incorporate its response into your answer."
            ),
            tools=[tool],
        ),
    )
    print(f"[A2A] Agent ready (id={agent.id}, name={agent.name}, version={agent.version})")
    return agent.name


def test_call(prompt: str = "What can the AWS agent do?") -> str:
    """Quick smoke test: forces the tool call so you can confirm the A2A
    connection actually works end-to-end."""
    settings = load_settings()
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)
    openai = project.get_openai_client()

    agent_name = create_or_update_collaborator_agent()

    response = openai.responses.create(
        tool_choice="required",
        input=prompt,
        extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    )
    print(response.output_text)
    return response.output_text


if __name__ == "__main__":
    test_call()
