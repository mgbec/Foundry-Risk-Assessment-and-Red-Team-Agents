"""
A2A client example
=====================
Calls a2a_target_agent.py's agent the way a genuinely EXTERNAL caller
would -- over the actual A2A protocol, using the open-source a2a-sdk,
rather than our own direct openai.responses.create() calls.

This is the test that actually answers the open question flagged in
a2a_target_agent.py: does a tool call (lookup_customer_account) get
resolved when the request arrives over A2A instead of driven by our own
process? Run this and read the reply -- if it correctly reports a balance
for ACC-1001, tool execution works over A2A; if it stalls, errors, or
replies without the looked-up data, the client-executed function-tool
pattern doesn't carry over to A2A callers and you'd need a different
approach (e.g. tools backed by something Foundry can call itself, rather
than a local Python function).

Requires: pip install a2a-sdk azure-identity httpx
(these are NOT in requirements.txt -- this script is a one-off diagnostic,
not part of the scan pipeline)

Usage:
    python a2a_client_example.py                                  # uses AZURE_AI_PROJECT_ENDPOINT + default agent name
    python a2a_client_example.py --agent-name my-other-agent
    python a2a_client_example.py --message "What can you do?"
"""
from __future__ import annotations

import argparse
import asyncio

import httpx
from azure.identity import DefaultAzureCredential

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest

from config import load_settings
from a2a_target_agent import DEFAULT_AGENT_NAME


async def call_agent(project_endpoint: str, agent_name: str, message: str) -> None:
    base_url = f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/a2a"

    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default").token

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(120.0),
    ) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url,
            agent_card_path="agentCard/v1.0",
        )
        agent_card = await resolver.get_agent_card()
        print(f"[A2AClient] Resolved agent card: {agent_card.name!r}, protocol {agent_card.protocol_version}")

        client = await create_client(
            agent=agent_card,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )

        request = SendMessageRequest(message=new_text_message(message, role=Role.ROLE_USER))
        print(f"[A2AClient] Sending: {message!r}")
        async for response in client.send_message(request):
            print(f"[A2AClient] Reply: {response}")

        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument("--message", default="What's the balance on account ACC-1001?")
    args = parser.parse_args()

    settings = load_settings()
    asyncio.run(call_agent(settings.project_endpoint, args.agent_name, args.message))
