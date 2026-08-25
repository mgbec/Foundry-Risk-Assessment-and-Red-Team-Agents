"""
Minimal, standards-compliant, public A2A test server.

Exists purely to test whether Foundry's outgoing A2A tool can successfully
call a genuine external, spec-compliant A2A agent -- as opposed to another
Foundry agent, which the repo's other A2A scripts found broken (see
agent/a2a_caller_agent.py and the README's "A2A (agent-to-agent) access"
section).

Deliberately trivial and unauthenticated: echoes back whatever it's asked
with a fixed marker string, so a reply containing that marker proves the
full round trip -- card discovery, task execution, content delivery --
actually works end to end, distinct from just getting a 200 back.

No secrets, no real functionality, no state worth protecting -- safe to
leave running as a disposable public diagnostic. Tear it down with:
    az containerapp delete --name a2a-test-server --resource-group rg-aisafety-redteam --yes
"""
from __future__ import annotations

import os

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from starlette.applications import Starlette

MARKER = "TEST-SERVER-CONFIRMED"


class EchoAgentExecutor(AgentExecutor):
    """Echoes the request back with MARKER prefixed, so the marker's
    presence in a caller's final reply proves content actually round-tripped."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        task_updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Processing..."),
        )

        query = get_message_text(context.message) or ""
        result = f"{MARKER}: you asked '{query}'"

        await task_updater.add_artifact(parts=[new_text_part(text=result, media_type="text/plain")])
        await task_updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("Done."),
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel is not supported.")


def build_app() -> Starlette:
    port = int(os.environ.get("PORT", 8080))
    # Set after first deploy, once Azure hands out the real public FQDN --
    # see a2a-test-server/deploy.md. Falls back to a placeholder so the
    # server still boots before that.
    public_url = os.environ.get("PUBLIC_URL", f"http://0.0.0.0:{port}")

    skill = AgentSkill(
        id="echo",
        name="Echo",
        description="Echoes back whatever text it's sent, with a fixed marker, to prove an A2A round trip works.",
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["a2a", "test", "echo"],
        examples=["hello"],
    )

    agent_card = AgentCard(
        name="A2A Test Echo Agent",
        description="Minimal public test agent for verifying outgoing A2A from Foundry Agent Service.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False, extended_agent_card=False),
        supported_interfaces=[
            # protocol_version deliberately left unset: agent_card_to_dict()
            # only backfills the legacy url/protocolVersion/preferredTransport
            # fields (which Foundry's outgoing A2A tool requires) when at
            # least one interface has an empty or legacy protocol_version --
            # setting it to "1.0" here made that compat conversion silently
            # produce nothing, which is exactly what broke this the first time.
            AgentInterface(protocol_binding="JSONRPC", url=public_url),
        ],
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=EchoAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    routes = []
    routes.extend(create_agent_card_routes(agent_card))
    routes.extend(create_jsonrpc_routes(request_handler, "/"))
    return Starlette(routes=routes)


app = build_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
