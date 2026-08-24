"""
A2A-callable target agent
===========================
Exposes the same fictional customer-support scenario as
sample_target_agent.py, but as a Prompt agent (the "responses protocol"
agent type) with incoming Agent2Agent (A2A) enabled -- so another agent,
anywhere, can discover and call it, not just our own scripts.

Why a separate file instead of just enabling A2A on sample_target_agent.py:
incoming A2A requires the responses protocol, and sample_target_agent.py is
built on the older Assistants-style API (AgentsClient.create_agent +
threads/runs) that orchestrator.py and red_team_agentic.py already depend
on. Rebuilding it here as a Prompt agent keeps that existing pipeline
untouched.

Reuses the tool implementations, fake data, and instructions from
sample_target_agent.py -- only the agent-creation and tool-schema plumbing
differs, because Prompt agents take an explicit JSON schema per tool
instead of introspecting a Python function's docstring.

CONFIRMED LIMITATION (tested 2026-08-24 against Foundry's preview
incoming-A2A endpoint via a2a_client_example.py): a real external A2A
caller gets back TASK_STATE_COMPLETED with no content whatsoever -- no
artifacts, no history, no status message -- regardless of what
lookup_customer_account/reset_customer_password did internally. The
run_conversation() smoke test below works fine because IT drives the
function-call loop itself (the documented Prompt-agent pattern: "your app
executes the function and returns the output"), which only proves the
agent works when driven directly, not over A2A. See the README's "A2A
(agent-to-agent) access" section for the full finding and next steps
(client.get_task() may be the intended way to fetch a completed task's
result -- untried here). This whole feature is in public preview.

Usage:
    python a2a_target_agent.py                # create/verify, enable A2A, smoke test
    python a2a_target_agent.py --no-smoke-test # skip the local test message
"""
from __future__ import annotations

import argparse
import json

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    A2AProtocolConfiguration,
    AgentCard,
    AgentCardSkill,
    AgentEndpointConfig,
    FunctionTool,
    PromptAgentDefinition,
    ProtocolConfiguration,
    ResponsesProtocolConfiguration,
)

from config import load_settings
from observability import trace_run
from sample_target_agent import (
    AGENT_INSTRUCTIONS,
    lookup_customer_account,
    reset_customer_password,
)

DEFAULT_AGENT_NAME = "a2a-sample-target-agent"

_ACCOUNT_ID_PARAM = {
    "type": "object",
    "properties": {
        "account_id": {"type": "string", "description": 'The account ID, e.g. "ACC-1001".'},
    },
    "required": ["account_id"],
    "additionalProperties": False,
}

TOOLS = [
    FunctionTool(
        name="lookup_customer_account",
        description="Looks up a customer account by ID. Returns name, email, and balance, or an error if not found.",
        parameters=_ACCOUNT_ID_PARAM,
        strict=True,
    ),
    FunctionTool(
        name="reset_customer_password",
        description="Resets the password for a customer account. Only call after identity has been verified.",
        parameters=_ACCOUNT_ID_PARAM,
        strict=True,
    ),
]

_TOOL_IMPLEMENTATIONS = {
    "lookup_customer_account": lookup_customer_account,
    "reset_customer_password": reset_customer_password,
}


def ensure_a2a_agent(settings, agent_name: str = DEFAULT_AGENT_NAME):
    """Creates a new version of the Prompt agent, returns (project, agent).
    Unlike the classic agent API, Prompt agents are versioned -- calling
    this again doesn't error or reuse, it just creates version N+1 under
    the same name. Fine for occasional smoke-testing; delete old versions
    with project.agents.delete_version() if you run this a lot."""
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())

    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=settings.target_deployment,
            instructions=AGENT_INSTRUCTIONS,
            tools=TOOLS,
        ),
    )
    print(f"[A2ATargetAgent] '{agent.name}' version {agent.version} ready (model={settings.target_deployment}).")
    return project, agent


def enable_incoming_a2a(project, agent, project_endpoint: str) -> str:
    """Enables the A2A protocol + agent card on the given agent. Returns the v1.0 agent card URL."""
    project.agents.update_details(
        agent_name=agent.name,
        agent_endpoint=AgentEndpointConfig(
            protocol_configuration=ProtocolConfiguration(
                responses=ResponsesProtocolConfiguration(),
                a2a=A2AProtocolConfiguration(),
            ),
        ),
        agent_card=AgentCard(
            version="1.0",
            description="Fictional Contoso Retail customer-support agent (synthetic data only).",
            skills=[
                AgentCardSkill(
                    id="account-lookup",
                    name="Account lookup",
                    description="Look up a customer account by ID.",
                ),
                AgentCardSkill(
                    id="password-reset",
                    name="Password reset",
                    description="Reset a customer's password after identity verification.",
                ),
            ],
        ),
    )
    card_url = f"{project_endpoint}/agents/{agent.name}/endpoint/protocols/a2a/agentCard/v1.0"
    print(f"[A2ATargetAgent] Incoming A2A enabled. Agent card: {card_url}")
    return card_url


def run_conversation(project, agent, message: str) -> str:
    """Drives one turn locally (not over A2A) -- executes any function_call
    items ourselves, matching the documented client-executed tool pattern.
    Useful to prove the agent + tools work before testing the real A2A path."""
    openai = project.get_openai_client()
    conversation = openai.conversations.create()

    response = openai.responses.create(
        input=message,
        conversation=conversation.id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
    )

    tool_outputs = []
    for item in response.output:
        if item.type == "function_call" and item.name in _TOOL_IMPLEMENTATIONS:
            result = _TOOL_IMPLEMENTATIONS[item.name](**json.loads(item.arguments))
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": result,
            })

    if tool_outputs:
        response = openai.responses.create(
            input=tool_outputs,
            conversation=conversation.id,
            extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        )

    openai.conversations.delete(conversation_id=conversation.id)
    return response.output_text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_AGENT_NAME, help="Agent name to create/reuse.")
    parser.add_argument("--no-smoke-test", action="store_true", help="Skip the end-to-end test message.")
    args = parser.parse_args()

    settings = load_settings()
    with trace_run("a2a-target-agent-run"):
        project, agent = ensure_a2a_agent(settings, args.name)
        card_url = enable_incoming_a2a(project, agent, settings.project_endpoint)

        if not args.no_smoke_test:
            reply = run_conversation(project, agent, "What's the balance on account ACC-1001?")
            print(f"[A2ATargetAgent] Smoke test reply: {reply}")

    print(
        f"\nWhoever should be allowed to call this over A2A needs the "
        f"'Foundry Agent Consumer' role on this project (see infra/main.tf).\n"
        f"Fetch the agent card yourself to verify:\n"
        f'  curl -H "Authorization: Bearer $(az account get-access-token '
        f'--resource https://ai.azure.com --query accessToken -o tsv)" {card_url}'
    )
