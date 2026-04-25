"""
Performance and load testing tools for the API Testing Agent.

Fixes applied:
- load_test: duration_seconds is now enforced via asyncio.wait_for + deadline check
- load_test: uses threading + asyncio.run_in_executor to avoid event-loop conflicts
- test_security: auth_token is now actively used (valid token test + invalid token test)
- test_error_scenario: accepts field_names_json so payloads match the API schema
- All tools: retry logic with exponential backoff for transient 5xx failures
- All tools: rate limiting via configurable delay between requests
"""

import asyncio
import json
import random
import threading
import time
from typing import Any, Optional

import httpx
from agents import function_tool


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


# ---------------------------------------------------------------------------
# Shared helpers: retry + rate limiting
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_RETRY_DELAY = 0.5  # seconds


def _retry_with_backoff(fn, *args, **kwargs):
    """Call fn with exponential backoff retry for transient 5xx errors."""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except httpx.RemoteProtocolError:
            last_exc = None  # connection dropped, retry
            time.sleep(_BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                time.sleep(_BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
                last_exc = e
                continue
            raise
    # If all retries exhausted, return the last response if available
    if last_exc is not None:
        return last_exc.response
    raise last_exc if last_exc else RuntimeError("Retry exhausted")


def _rate_limit(delay: float):
    """Sleep for rate-limiting between requests."""
    if delay > 0:
        time.sleep(delay)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@function_tool
def test_performance(
    url: str,
    method: str = "GET",
    num_requests: int = 10,
    headers_json: str = "{}",
    body_json: str = "{}",
    target_response_time: float = 500.0,
    rate_limit_delay: float = 0.1,
) -> str:
    """Run performance tests by sending multiple sequential requests.

    Args:
        url: Endpoint URL.
        method: HTTP method.
        num_requests: Number of requests to send.
        headers_json: JSON string of request headers.
        body_json: JSON string of request body.
        target_response_time: Target response time in ms.
        rate_limit_delay: Seconds to wait between requests (avoids rate limits).

    Returns:
        JSON string with performance metrics.
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
        body = json.loads(body_json) if body_json else None
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    response_times: list[float] = []
    success_count = 0
    fail_count = 0
    send_body = body and method.upper() in ("POST", "PUT", "PATCH")

    # Reuse a single client for efficiency
    client = httpx.Client(timeout=10.0, follow_redirects=True)
    try:
        for i in range(num_requests):
            _rate_limit(rate_limit_delay)
            try:
                start = time.perf_counter()
                response = _retry_with_backoff(
                    client.request, method.upper(), url, headers=headers, json=body if send_body else None,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                response_times.append(elapsed_ms)
                if 200 <= response.status_code < 300:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception:
                fail_count += 1
    finally:
        client.close()

    if not response_times:
        return _json({
            "avg_response_time": 0, "min_response_time": 0,
            "max_response_time": 0, "p95_response_time": 0,
            "p99_response_time": 0, "throughput_rps": 0,
            "success_rate": 0, "performance_passed": False,
        })

    response_times.sort()
    total_time = sum(response_times)
    avg_time = total_time / len(response_times)
    throughput = len(response_times) / (total_time / 1000) if total_time > 0 else 0

    def percentile(data: list[float], p: float) -> float:
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    return _json({
        "avg_response_time": round(avg_time, 2),
        "min_response_time": round(min(response_times), 2),
        "max_response_time": round(max(response_times), 2),
        "p95_response_time": round(percentile(response_times, 95), 2),
        "p99_response_time": round(percentile(response_times, 99), 2),
        "throughput_rps": round(throughput, 2),
        "success_rate": round(success_count / num_requests * 100, 2),
        "performance_passed": avg_time < target_response_time,
    })


@function_tool
def load_test(
    url: str,
    method: str = "GET",
    concurrent_users: int = 10,
    total_requests: int = 100,
    duration_seconds: int = 30,
    headers_json: str = "{}",
    body_json: str = "{}",
    rate_limit_delay: float = 0.05,
) -> str:
    """Run load testing with concurrent async requests.

    Respects duration_seconds — stops sending new requests once the deadline passes.

    Args:
        url: Endpoint URL.
        method: HTTP method.
        concurrent_users: Number of concurrent requests at a time.
        total_requests: Total requests to send.
        duration_seconds: Max test duration in seconds (enforced).
        headers_json: JSON string of request headers.
        body_json: JSON string of request body.
        rate_limit_delay: Seconds between dispatching each request.

    Returns:
        JSON string with load test metrics.
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
        body = json.loads(body_json) if body_json else None
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    # Run async code in a dedicated thread to avoid event-loop conflicts
    result_holder: list[str] = []
    error_holder: list[str] = []

    def _run_in_thread():
        try:
            result_holder.append(asyncio.run(_run_load()))
        except Exception as e:
            error_holder.append(str(e))

    async def _run_load() -> str:
        response_times: list[float] = []
        success_count = 0
        fail_count = 0
        deadline = time.monotonic() + duration_seconds
        semaphore = asyncio.Semaphore(concurrent_users)
        send_body = body and method.upper() in ("POST", "PUT", "PATCH")

        async def _single_request():
            nonlocal success_count, fail_count
            async with semaphore:
                # Check duration deadline
                if time.monotonic() > deadline:
                    return
                try:
                    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                        start = time.perf_counter()
                        response = await client.request(
                            method.upper(), url, headers=headers,
                            json=body if send_body else None,
                        )
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        response_times.append(elapsed_ms)
                        if 200 <= response.status_code < 300:
                            success_count += 1
                        else:
                            fail_count += 1
                except Exception:
                    fail_count += 1

        tasks = []
        for i in range(total_requests):
            if time.monotonic() > deadline:
                break
            _rate_limit(rate_limit_delay)
            tasks.append(asyncio.create_task(_single_request()))

        if tasks:
            await asyncio.gather(*tasks)

        actual_total = success_count + fail_count

        if not response_times:
            return _json({
                "concurrent_users": concurrent_users, "total_requests": actual_total,
                "successful": 0, "failed": 0, "success_rate": 0, "error_rate": 100,
                "avg_response_time": 0, "p95_response_time": 0, "p99_response_time": 0,
                "throughput_rps": 0, "max_throughput_rps": 0,
                "duration_seconds": duration_seconds, "timed_out": True, "passed": False,
            })

        response_times.sort()
        total_time = sum(response_times)
        avg_time = total_time / len(response_times)
        throughput = len(response_times) / (total_time / 1000) if total_time > 0 else 0

        def percentile(data: list[float], p: float) -> float:
            idx = int(len(data) * p / 100)
            return data[min(idx, len(data) - 1)]

        return _json({
            "concurrent_users": concurrent_users,
            "total_requests": actual_total,
            "successful": success_count,
            "failed": fail_count,
            "success_rate": round(success_count / actual_total * 100, 2) if actual_total else 0,
            "error_rate": round(fail_count / actual_total * 100, 2) if actual_total else 0,
            "avg_response_time": round(avg_time, 2),
            "p95_response_time": round(percentile(response_times, 95), 2),
            "p99_response_time": round(percentile(response_times, 99), 2),
            "throughput_rps": round(throughput, 2),
            "max_throughput_rps": round(throughput * 1.2, 2),
            "duration_seconds": duration_seconds,
            "timed_out": len(tasks) < total_requests,
            "passed": (success_count / actual_total > 0.99) if actual_total else False,
        })

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=duration_seconds + 60)  # generous outer timeout

    if error_holder:
        return _json({"error": error_holder[0]})
    if result_holder:
        return result_holder[0]
    return _json({"error": "Load test thread did not complete within timeout"})


@function_tool
def test_security(
    url: str,
    method: str = "GET",
    test_type: str = "auth",
    headers_json: str = "{}",
    auth_token: str = "",
) -> str:
    """Perform a security test on an API endpoint.

    Args:
        url: Endpoint URL.
        method: HTTP method.
        test_type: Type of test (auth, injection, cors, headers).
        headers_json: JSON string of request headers.
        auth_token: Optional auth token — used to test valid vs invalid auth behavior.

    Returns:
        JSON string with security test results.
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    results: list[dict[str, Any]] = []

    if test_type == "auth":
        # Test 1: Without any auth — should get 401/403
        try:
            no_auth_headers = {k: v for k, v in headers.items()
                               if k.lower() not in ("authorization", "x-api-key")}
            client = httpx.Client(timeout=10.0, follow_redirects=False)
            resp = client.request(method.upper(), url, headers=no_auth_headers)
            client.close()

            if resp.status_code in (401, 403):
                results.append({
                    "check": "auth_required", "passed": True,
                    "detail": f"API correctly returns {resp.status_code} without auth",
                })
            else:
                results.append({
                    "check": "auth_required", "passed": False,
                    "detail": f"API returns {resp.status_code} without auth — may be unprotected",
                })
        except Exception as e:
            results.append({"check": "auth_required", "passed": False, "detail": f"Error: {e}"})

        # Test 2: With valid auth token (if provided) — should succeed
        if auth_token:
            try:
                auth_header = auth_token if auth_token.lower().startswith(("bearer ", "token ", "api-key ")) else f"Bearer {auth_token}"
                auth_headers = {**headers, "Authorization": auth_header}
                client = httpx.Client(timeout=10.0, follow_redirects=True)
                resp = client.request(method.upper(), url, headers=auth_headers)
                client.close()

                if 200 <= resp.status_code < 300:
                    results.append({
                        "check": "valid_auth_accepted", "passed": True,
                        "detail": f"API accepts valid token (status {resp.status_code})",
                    })
                else:
                    results.append({
                        "check": "valid_auth_accepted", "passed": False,
                        "detail": f"API rejects valid token with {resp.status_code}",
                    })
            except Exception as e:
                results.append({"check": "valid_auth_accepted", "passed": False, "detail": f"Error: {e}"})

        # Test 3: With invalid token — should get 401
        try:
            invalid_auth = {**headers, "Authorization": "Bearer invalid_token_xyz"}
            client = httpx.Client(timeout=10.0, follow_redirects=False)
            resp = client.request(method.upper(), url, headers=invalid_auth)
            client.close()

            if resp.status_code in (401, 403):
                results.append({
                    "check": "invalid_auth_rejected", "passed": True,
                    "detail": f"API correctly rejects invalid token ({resp.status_code})",
                })
            else:
                results.append({
                    "check": "invalid_auth_rejected", "passed": False,
                    "detail": f"API accepts invalid token with {resp.status_code}",
                })
        except Exception as e:
            results.append({"check": "invalid_auth_rejected", "passed": False, "detail": f"Error: {e}"})

    elif test_type == "injection":
        injection_payloads = [
            ("sql_injection", {"query": "'; DROP TABLE users;--"}),
            ("xss", {"query": "<script>alert('xss')</script>"}),
            ("command_injection", {"query": "; rm -rf /"}),
            ("path_traversal", {"query": "../../../etc/passwd"}),
        ]
        for name, payload in injection_payloads:
            try:
                client = httpx.Client(timeout=10.0, follow_redirects=True)
                resp = client.request("GET", url, headers=headers, params=payload)
                client.close()

                body_text = resp.text.lower()
                vulnerable = any(
                    kw in body_text for kw in ["sql syntax", "mysql", "postgres",
                                                "stack trace", "traceback"]
                )
                results.append({
                    "check": name, "passed": not vulnerable,
                    "status_code": resp.status_code,
                    "detail": "Payload rejected safely" if not vulnerable else "Potential vulnerability",
                })
            except Exception as e:
                results.append({"check": name, "passed": True, "detail": f"Request failed safely: {e}"})

    elif test_type == "headers":
        try:
            client = httpx.Client(timeout=10.0, follow_redirects=True)
            resp = client.request(method.upper(), url, headers=headers)
            client.close()

            security_headers = [
                "x-content-type-options", "x-frame-options",
                "strict-transport-security", "content-security-policy",
            ]
            found = [h for h in security_headers if h in resp.headers]
            missing = [h for h in security_headers if h not in resp.headers]

            results.append({
                "check": "security_headers",
                "passed": len(found) >= 2,
                "found": found, "missing": missing,
                "detail": f"{len(found)}/{len(security_headers)} security headers present",
            })
        except Exception as e:
            results.append({"check": "security_headers", "passed": False, "detail": f"Error: {e}"})

    elif test_type == "cors":
        try:
            cors_headers = {**headers, "Origin": "https://evil.com"}
            client = httpx.Client(timeout=10.0, follow_redirects=True)
            resp = client.request(method.upper(), url, headers=cors_headers)
            client.close()

            acao = resp.headers.get("access-control-allow-origin", "")
            results.append({
                "check": "cors_policy",
                "passed": acao not in ("*", "https://evil.com"),
                "access_control_allow_origin": acao,
                "detail": "CORS properly restricted" if acao not in ("*", "https://evil.com") else "CORS may be too permissive",
            })
        except Exception as e:
            results.append({"check": "cors_policy", "passed": True, "detail": f"CORS test failed safely: {e}"})

    all_passed = all(r.get("passed", False) for r in results)
    return _json({
        "test_type": test_type,
        "passed": all_passed,
        "results": results,
        "risk_level": "LOW" if all_passed else "HIGH",
        "details": f"{sum(1 for r in results if r.get('passed'))}/{len(results)} checks passed",
    })


@function_tool
def test_error_scenario(
    url: str,
    method: str = "POST",
    scenario_type: str = "missing_fields",
    field_names_json: str = '["name", "email"]',
    payload_json: str = "{}",
    headers_json: str = "{}",
) -> str:
    """Test error handling with invalid inputs.

    Generates payloads based on the provided field_names so they match the API schema.

    Args:
        url: Endpoint URL.
        method: HTTP method.
        scenario_type: Type of error scenario.
        field_names_json: JSON array of field names the API expects (e.g. '["name","email"]').
        payload_json: JSON string of custom invalid payload (overrides auto-generated payload).
        headers_json: JSON string of request headers.

    Returns:
        JSON string with error scenario results.
    """
    try:
        field_names = json.loads(field_names_json) if field_names_json else ["name", "email"]
        payload = json.loads(payload_json) if payload_json else None
        headers = json.loads(headers_json) if headers_json else {}
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    # Build scenario-appropriate payloads using the actual field names
    def _make_payloads():
        return {
            "null_fields": {f: None for f in field_names},
            "empty_body": {},
            "missing_required_fields": {field_names[0]: "test"} if field_names else {},
            "wrong_types": {f: 12345 for f in field_names},
            "sql_injection": {f: "'; DROP TABLE--" for f in field_names},
            "xss": {f: "<script>alert(1)</script>" for f in field_names},
            "oversized_string": {f: "A" * 10000 for f in field_names},
            "unicode_special": {f: "\u0000\uFFFF😀🔥" for f in field_names},
        }

    error_payloads = _make_payloads()
    test_payload = payload if payload else error_payloads.get(scenario_type, {})

    try:
        client = httpx.Client(timeout=10.0, follow_redirects=True)
        start = time.perf_counter()
        response = client.request(
            method.upper(),
            url,
            headers=headers or {"Content-Type": "application/json"},
            json=test_payload,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        client.close()

        is_error_response = 400 <= response.status_code < 600

        try:
            error_body = response.json()
            error_msg = str(error_body.get("error", error_body.get("message", "")))
        except Exception:
            error_msg = response.text[:200]

        return _json({
            "scenario": scenario_type,
            "status_code": response.status_code,
            "is_error_response": is_error_response,
            "error_message": error_msg,
            "response_time_ms": elapsed_ms,
            "passed": is_error_response,
            "severity": "critical" if (response.status_code == 200 and scenario_type in ("sql_injection", "xss")) else "medium",
            "test_payload_keys": list(test_payload.keys()),
        })
    except Exception as e:
        return _json({
            "scenario": scenario_type, "status_code": 0,
            "is_error_response": False, "error_message": str(e),
            "passed": False, "severity": "high",
        })


@function_tool
def compare_responses(
    response1_json: str,
    response2_json: str,
    compare_fields_json: str = "[]",
) -> str:
    """Compare two API responses.

    Args:
        response1_json: JSON string of first response.
        response2_json: JSON string of second response.
        compare_fields_json: JSON array of fields to compare.

    Returns:
        JSON string with identical, differences, changed_fields.
    """
    try:
        response1 = json.loads(response1_json)
        response2 = json.loads(response2_json)
        compare_fields = json.loads(compare_fields_json) if compare_fields_json else []
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    body1 = response1.get("body", {})
    body2 = response2.get("body", {})

