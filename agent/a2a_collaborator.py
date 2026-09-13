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

import argparse
import os
import sys

# The SCF agent's replies can contain emoji/unicode; Windows' default cp1252
# stdout raises UnicodeEncodeError trying to print them. Force UTF-8 so the
# answer prints cleanly regardless of platform/console codepage.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import uuid

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, A2APreviewTool
from opentelemetry import trace

from config import load_settings
from observability import enable_tracing

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


def ask(prompt: str, agent_name: str | None = None, *, openai=None) -> str:
    """Send one question through the collaborator agent, which delegates to
    the AWS SCF agent over A2A and returns its answer.

    Pass an existing agent_name (and openai client) to reuse them across
    calls -- e.g. the interactive loop below -- instead of recreating the
    agent every turn. tool_choice="required" forces the A2A delegation so a
    smoke test can't be silently answered by the coordinator model itself.

    OBSERVABILITY: every A2A call is wrapped in an OpenTelemetry span. When
    APPLICATIONINSIGHTS_CONNECTION_STRING is set the span is exported to
    Application Insights; otherwise it's a harmless no-op (same pattern as
    observability.trace_run). Each call gets an "a2a.correlation_id" -- logged
    to stdout AND set as a span attribute -- so an Azure-side trace can be
    joined to the AWS side: grep that id in the collaborator's stdout, then
    line it up (by timestamp) with the API Gateway access log group
    /aws/apigateway/scf-agent-a2a. A2APreviewTool doesn't expose a way to
    inject a custom header into the outgoing A2A request, so this is a
    logged/attributed correlation id rather than one propagated in the wire
    call -- see docs/a2a-threat-model.md (T5.1) for the remaining gap.
    """
    if openai is None:
        settings = load_settings()
        project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
        openai = project.get_openai_client()
    if agent_name is None:
        agent_name = create_or_update_collaborator_agent()

    correlation_id = uuid.uuid4().hex
    enable_tracing()
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("a2a.collaborator.ask") as span:
        span.set_attribute("a2a.correlation_id", correlation_id)
        span.set_attribute("a2a.agent_name", agent_name)
        span.set_attribute("a2a.prompt_length", len(prompt))
        span.set_attribute("a2a.target_connection", A2A_CONNECTION_NAME)
        print(f"[A2A] correlation_id={correlation_id} agent={agent_name}")
        try:
            response = openai.responses.create(
                tool_choice="required",
                input=prompt,
                extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
            )
        except Exception as exc:  # noqa: BLE001 -- record on the span, then re-raise
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise
        span.set_attribute("a2a.response_length", len(response.output_text or ""))
        return response.output_text


def test_call(prompt: str = "What can the AWS agent do?") -> str:
    """Quick smoke test: forces the tool call so you can confirm the A2A
    connection actually works end-to-end."""
    answer = ask(prompt)
    print(answer)
    return answer


def interactive() -> None:
    """Chat loop: create the collaborator agent once, then relay each typed
    question to the AWS SCF agent over A2A. Type 'quit' or 'exit' to stop."""
    settings = load_settings()
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
    openai = project.get_openai_client()
    agent_name = create_or_update_collaborator_agent()

    print("Ask the AWS SCF Compliance agent a question (type 'quit' to exit).")
    print('e.g. "Look up SCF control IAC-15 and show the Level 2 and 3 maturity criteria"\n')
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt.lower() in ("quit", "exit"):
            break
        try:
            print(f"scf> {ask(prompt, agent_name, openai=openai)}\n")
        except Exception as exc:  # noqa: BLE001 -- keep the loop alive on a transient error
            print(f"[error] {exc}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Query the AWS SCF Compliance agent through a Foundry collaborator agent over A2A."
    )
    parser.add_argument(
        "-m", "--message",
        help="A single question to send. Omit to run the built-in smoke test, "
             "or use --interactive for a chat loop.",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Start an interactive chat loop (reuses one collaborator agent across turns).",
    )
    args = parser.parse_args()

    if args.interactive:
        interactive()
    elif args.message:
        print(ask(args.message))
    else:
        test_call()
