"""
Orchestrator — coordinates all sub-agents via the OpenAI Agents SDK.
"""

import json
from typing import Any, Optional

from agents import Agent, Runner, handoff

from .agents import (
    create_error_handling_tester_agent,
    create_functional_tester_agent,
    create_load_tester_agent,
    create_performance_tester_agent,
    create_security_tester_agent,
)

ORCHESTRATOR_PROMPT = """You are the Master API Testing Agent orchestrating comprehensive API quality assurance.

Testing Strategy — execute these phases in order:
1. PHASE 1: Endpoint Discovery & Basic Connectivity — use discover_endpoints tool, then test base URL
2. PHASE 2: Functional Testing — validate endpoints, data, edge cases
3. PHASE 3: Performance Testing — response times, throughput
4. PHASE 4: Security Testing — auth, injection, CORS, headers
5. PHASE 5: Load Testing — concurrent requests, stability
6. PHASE 6: Analysis & Reporting — consolidate results, score, recommend

IMPORTANT: The auth_token below is provided for testing. Always include it as
"Authorization": "Bearer <token>" in headers_json when calling send_request and other tools
that require authentication.

Quality Scoring (1-10):
- 9-10: Production-ready, all tests pass
- 7-8: Good, minor issues
- 5-6: Acceptable, some issues to address
- 3-4: Poor, multiple critical issues
- 1-2: Not ready, fundamental problems

For each phase:
- Describe what tests were executed
- Report pass/fail counts
- List critical issues found
- Give next-phase recommendation

At the end produce a JSON report with:
{
  "api_name": "...",
  "base_url": "...",
  "phases": { ... },
  "quality_score": N,
  "rating": "...",
  "executive_summary": "...",
  "critical_issues": [...],
  "recommendations": [...]
}

Delegate to the appropriate sub-agent for each phase. Collect their results and consolidate."""


def _build_auth_headers(auth_token: Optional[str]) -> str:
    """Build Authorization header JSON string from auth token."""
    if not auth_token:
        return "{}"
    token = auth_token
    if not token.lower().startswith(("bearer ", "token ", "api-key ")):
        token = f"Bearer {token}"
    return json.dumps({"Authorization": token})


def build_orchestrator(
    api_name: str,
    base_url: str,
    auth_token: Optional[str] = None,
    endpoints: Optional[list[dict[str, Any]]] = None,
) -> Agent:
    """Build the master orchestrator agent with handoffs to sub-agents."""

    functional = create_functional_tester_agent()
    performance = create_performance_tester_agent()
    security = create_security_tester_agent()
    load = create_load_tester_agent()
    error_handling = create_error_handling_tester_agent()

    # Build context for the orchestrator
    auth_headers_json = _build_auth_headers(auth_token)
    context_parts = [
        f"API Name: {api_name}",
        f"Base URL: {base_url}",
        f"Auth Headers (use in headers_json): {auth_headers_json}",
    ]
    if endpoints:
        context_parts.append(f"Endpoints to test: {json.dumps(endpoints, indent=2)}")

    orchestrator = Agent(
        name="API Testing Orchestrator",
        model="gpt-4o",
        instructions=ORCHESTRATOR_PROMPT + "\n\n--- Current Test Context ---\n" + "\n".join(context_parts),
        handoffs=[
            handoff(functional),
            handoff(performance),
            handoff(security),
            handoff(load),
            handoff(error_handling),
        ],
    )

    return orchestrator
async def run_full_test_suite(
    api_name: str,
    base_url: str,
    auth_token: Optional[str] = None,
    endpoints: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Run the complete API test suite via the orchestrator agent.

    Returns the final text output from the agent (report).
    """
    orchestrator = build_orchestrator(api_name, base_url, auth_token, endpoints)

    user_prompt = (
        f"Run the full 6-phase test suite for the API '{api_name}' at {base_url}."
    )
    if endpoints:
        user_prompt += f" Test these specific endpoints: {json.dumps(endpoints, indent=2)}"
    else:
        user_prompt += " Start by using the discover_endpoints tool to find available endpoints."

    result = await Runner.run(orchestrator, input=user_prompt)

    return result.final_output


