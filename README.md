# API Testing Agent

AI-powered API testing agent built with the **OpenAI Agents SDK**. Coordinates multiple specialized sub-agents (Functional, Performance, Security, Load, Error Handling) to comprehensively test any REST API — from discovery through reporting.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      CLI (cli.py)                       │
│   argparse → resolve API key → call run_full_test_suite │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              Orchestrator (orchestrator.py)              │
│   Master Agent (gpt-4o) — 6-phase testing strategy      │
│   handoff() → delegates to sub-agents per phase         │
└──┬──────────┬──────────┬──────────┬──────────┬──────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│Functional│ │Perform.│ │Security│ │  Load  │ │ Error  │
│ Tester │ │ Tester │ │ Tester │ │ Tester │ │Handling│
└───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
    │          │          │          │          │
    └──────────┴──────────┴──────────┴──────────┘
                         │
              ┌──────────▼──────────┐
              │    Tool Layer       │
              │  (httpx requests)   │
              └─────────────────────┘
```

### Design Pattern

The project uses a **hierarchical multi-agent architecture** with three layers:

1. **CLI Layer** — Parses arguments, resolves API key, loads endpoint configs
2. **Orchestrator Layer** — Master Agent with `handoff()` to 5 sub-agents
3. **Tool Layer** — 8 `@function_tool`-decorated functions making real HTTP calls

The OpenAI Agents SDK's `handoff()` mechanism enables the LLM to dynamically transfer control between agents. The orchestrator's system prompt defines the 6-phase workflow, and the LLM decides which sub-agent to delegate to at each step.

---

## Testing Phases

| Phase | Name | Agent | What It Does |
|-------|------|-------|-------------|
| 1 | **Discovery** | Orchestrator | Tests base URL connectivity, verifies API is responding |
| 2 | **Functional** | Functional Tester | Validates endpoints, status codes, required fields, data types, parameters |
| 3 | **Performance** | Performance Tester | Sequential benchmarks (1/10/50/100 requests), throughput, percentiles |
| 4 | **Security** | Security Tester | Auth requirements, SQL injection, XSS, CORS, security headers |
| 5 | **Load** | Load Tester | Concurrent users (10/50/100), ramp-up, spike tests, breaking point |
| 6 | **Reporting** | Orchestrator | Consolidates results, quality score (1-10), executive summary, recommendations |

### Quality Scoring Scale

| Score | Rating | Criteria |
|-------|--------|----------|
| 9-10 | Production Ready | All tests pass, <200ms avg, >100 RPS, <0.1% error rate |
| 7-8 | Good | Minor issues, <500ms avg, >50 RPS, <1% error rate |
| 5-6 | Acceptable | Issues to address, 500ms-2s avg, >10 RPS, 1-5% error rate |
| 3-4 | Poor | Critical issues, >2s avg, <10 RPS, >5% error rate |
| 1-2 | Not Ready | Fundamental problems, endpoints not responding |

---

## Sub-Agents

### Functional Tester
- **Role**: Verify endpoints exist and respond correctly
- **Checks**: Status codes, Content-Type, required fields, data types, value ranges, ISO timestamps
- **Tools**: `send_request`, `validate_response`, `check_data_type`, `extract_data`, `test_error_scenario`

### Performance Tester
- **Role**: Measure response times and throughput
- **Thresholds**: Excellent <200ms, Good 200-500ms, Acceptable 500ms-1s, Poor 1-3s, Unacceptable >3s
- **Tools**: `send_request`, `test_performance`, `load_test`, `compare_responses`

### Security Tester
- **Role**: Identify vulnerabilities
- **Tests**: Auth bypass, SQL injection, XSS, command injection, path traversal, CORS policy, security headers
- **Risk Levels**: CRITICAL, HIGH, MEDIUM, LOW
- **Tools**: `send_request`, `test_security`, `test_error_scenario`, `validate_response`

### Load Tester
- **Role**: Test API under concurrent stress
- **Pass Criteria**: >99% success rate, <1% error rate, P95 <2x baseline
- **Tools**: `send_request`, `load_test`, `test_performance`, `compare_responses`

### Error Handling Tester
- **Role**: Validate error responses and edge cases
- **Scenarios**: Null fields, empty body, missing fields, SQL injection, XSS, oversized strings
- **Tools**: `send_request`, `test_error_scenario`, `validate_response`, `extract_data`

---

## Tool Functions

| Tool | Source | Description | Key Parameters |
|------|--------|-------------|----------------|
| `send_request` | `tools.py` | HTTP request with timing | `url`, `method`, `headers_json`, `body_json`, `timeout` |
| `validate_response` | `tools.py` | Validate status, fields, types | `response_json`, `expected_status`, `required_fields_json`, `field_types_json` |
| `check_data_type` | `tools.py` | Validate a single value's type | `value_str`, `expected_type` |
| `extract_data` | `tools.py` | Extract via dot-notation path | `response_json`, `path` |
| `test_performance` | `perf_tools.py` | Sequential performance benchmark | `url`, `num_requests`, `target_response_time` |
| `load_test` | `perf_tools.py` | Concurrent async load test | `url`, `concurrent_users`, `total_requests` |
| `test_security` | `perf_tools.py` | Auth/injection/CORS/header checks | `url`, `test_type` (auth/injection/cors/headers) |
| `test_error_scenario` | `perf_tools.py` | Invalid input / edge case test | `url`, `scenario_type`, `payload_json` |
| `compare_responses` | `perf_tools.py` | Diff two API responses | `response1_json`, `response2_json`, `compare_fields_json` |

> **Note**: All tools accept complex parameters as JSON strings (e.g., `headers_json: '{"Authorization": "Bearer xyz"}'`). This ensures compatibility with the OpenAI Agents SDK's strict JSON schema requirements.

---

## Setup

```bash
# Clone / navigate to the project
cd /home/waleed/Documents/API_tester

# Install dependencies (uses uv)
uv sync

# Set your OpenAI API key
export OPENAI_API_KEY="sk-proj-..."
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `openai-agents` | >=0.0.1 | Multi-agent framework (Agent, Runner, handoff, function_tool) |
| `httpx` | >=0.27 | HTTP client (sync + async) |
| `python-dotenv` | >=1.0 | Load `.env` files |
| **Python** | >=3.12 | Required |

---

## Usage

### Basic — Test any public API

```bash
api-tester https://jsonplaceholder.typicode.com
```

### Named API with authentication

```bash
api-tester https://api.example.com/v1 \
  --name "User Management API" \
  --auth-token "Bearer eyJhbGciOiJIUzI1NiIs..."
```

### Test specific endpoints

```bash
# As a JSON string
api-tester https://api.example.com/v1 \
  --endpoints '[
    {"path": "/users", "method": "GET", "required_fields": ["id", "name", "email"]},
    {"path": "/users", "method": "POST", "required_body_fields": ["name", "email"]}
  ]'

# Or from a JSON file
api-tester https://api.example.com/v1 --endpoints endpoints.json
```

### Save report to file

```bash
api-tester https://api.example.com/v1 --output report.json
```

### Pass API key directly

```bash
api-tester https://api.example.com/v1 --openai-api-key "sk-proj-..."
```

### Full example

```bash
api-tester https://api.example.com/v1 \
  --name "Payment API" \
  --auth-token "Bearer xyz" \
  --endpoints endpoints.json \
  --output payment_api_report.json \
  --openai-api-key "sk-proj-..."
```

---

## CLI Reference

```
usage: api-tester [-h] [--name NAME] [--auth-token AUTH_TOKEN]
                  [--endpoints ENDPOINTS] [--output OUTPUT]
                  [--openai-api-key OPENAI_API_KEY]
                  base_url

API Testing Agent — AI-powered API testing with OpenAI Agents SDK

positional arguments:
  base_url              Base URL of the API to test

options:
  -h, --help            Show help
  --name, -n            Name of the API (default: "Target API")
  --auth-token, -t      Authentication token (Bearer token or API key)
  --endpoints, -e       Endpoints as JSON string or .json file path
  --output, -o          Path to save the test report (default: stdout)
  --openai-api-key      OpenAI API key (or set OPENAI_API_KEY env var)
```

---

## Project Structure

```
src/api_tester/
├── __init__.py       # Package entry, main() → asyncio.run(cli_entry())
├── cli.py            # argparse CLI, endpoint file loading, error handling
├── orchestrator.py   # build_orchestrator() + run_full_test_suite()
├── agents.py         # 5 sub-agent factory functions + system prompts
├── tools.py          # send_request, validate_response, check_data_type, extract_data
└── perf_tools.py     # test_performance, load_test, test_security, test_error_scenario, compare_responses

pyproject.toml        # Project config, dependencies, console script entry point
README.md             # This file
```

---

## Known Limitations & Improvements

### Current Limitations

1. **`load_test` — `duration_seconds` parameter is accepted but not enforced** — the function runs all `total_requests` regardless of time. A time-limit mechanism could be added via `asyncio.wait_for`.

2. **`test_security` — `auth_token` parameter is defined but not actively used** — the auth test currently only checks for missing auth, not valid vs invalid token behavior.

3. **`load_test` calls `asyncio.run()` internally** — if the Agents SDK ever executes tools inside an already-running event loop, this would raise `RuntimeError`. Consider using `asyncio.get_event_loop().run_until_complete()` or a thread-based approach.

4. **No endpoint auto-discovery tool** — Phase 1 mentions discovery but there's no tool to fetch OpenAPI specs (`/openapi.json`, `/swagger.json`). The LLM probes the base URL manually, which is unreliable.

5. **Auth token is not auto-injected** — the token is passed to the orchestrator's context but not automatically added to tool calls. The LLM must remember to include it in `headers_json`.

6. **Error scenario payloads are hardcoded** — `test_error_scenario` uses `{"name": ..., "email": ...}` payloads, which assume the API accepts these fields. APIs with different schemas get meaningless test data.

7. **No rate limiting or backoff** — rapid requests could trigger rate limits or get the tester's IP blocked on production APIs.

8. **No retry logic** — transient failures (503, connection resets) are treated as hard failures.

### Suggested Improvements

- Add a `discover_endpoints` tool that fetches `/openapi.json` or `/docs` and parses endpoint definitions
- Auto-inject `Authorization` header from `--auth-token` into all tool calls
- Add configurable test intensity presets (smoke test vs full regression)
- Add structured logging for orchestrator and sub-agent activity
- Add Pydantic model validation for the final report output
- Add per-endpoint configuration (expected status codes, custom headers, validation rules)
- Add unit/integration tests for the tool functions
- Add a timeout parameter to `Runner.run()` to prevent hangs

---

## License

MIT
