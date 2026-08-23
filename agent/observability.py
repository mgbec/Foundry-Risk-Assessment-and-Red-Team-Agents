"""
Optional tracing: sends OpenTelemetry spans for the Azure SDK calls each
script makes (agent/thread/run calls, chat completions) to Azure Monitor
Application Insights, so a scan shows you the actual call trace instead of
just the final scorecard.

Entirely opt-in. If APPLICATIONINSIGHTS_CONNECTION_STRING isn't set,
`trace_run()` is a no-op and nothing behaves differently. Get the
connection string with:
    terraform output -raw app_insights_connection_string

Content capture (prompts, tool arguments, responses) is OFF by default.
This pipeline's traces routinely include red-team attack prompts and the
synthetic account data from sample_target_agent.py -- think about where
that then lives (Application Insights retention/access control) before
setting OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from opentelemetry import trace

_tracer = None
_attempted = False


def enable_tracing() -> None:
    """Configures Azure Monitor OpenTelemetry export if a connection string
    is set. Safe to call more than once -- only does the real work once."""
    global _tracer, _attempted
    if _attempted:
        return
    _attempted = True

    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return

    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=connection_string)
    _tracer = trace.get_tracer(__name__)
    print("[Observability] Tracing enabled -> Application Insights.")


@contextmanager
def trace_run(span_name: str):
    """Wraps a block in a named span if tracing is configured; a harmless
    no-op otherwise. Use one of these per script run so each scan/smoke-test
    shows up as a single top-level trace in Application Insights."""
    enable_tracing()
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(span_name):
        yield
