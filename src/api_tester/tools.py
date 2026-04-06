"""
HTTP tool functions for the API Testing Agent.
Wraps httpx calls with timing, error handling, retry logic, and rate limiting.

All tool functions use simple JSON-serializable types (str, int, float, bool, list)
and return JSON strings so the OpenAI Agents SDK can build strict schemas.
"""

import json
import random
import time
from typing import Any, Optional

import httpx
from agents import function_tool


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_RETRY_DELAY = 0.5  # seconds


def _retry_with_backoff(fn, *args, **kwargs):
    """Call fn with exponential backoff retry for transient 5xx / connection errors."""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except httpx.ConnectError:
            last_exc = None  # connection dropped, retry
            time.sleep(_BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
        except httpx.TimeoutException:
            last_exc = None
            time.sleep(_BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                time.sleep(_BASE_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 0.3))
                last_exc = e
                continue
            raise
    if last_exc is not None:
        return last_exc.response
    raise last_exc if last_exc else RuntimeError("Retry exhausted")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@function_tool
def send_request(
    url: str,
    method: str = "GET",
    headers_json: str = "{}",
    body_json: str = "{}",
    timeout: float = 10.0,
) -> str:
    """Send an HTTP request to an API endpoint.

    Args:
        url: Full API endpoint URL.
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        headers_json: JSON string of HTTP headers.
        body_json: JSON string of request body.
        timeout: Timeout in seconds.

    Returns:
        JSON string with status_code, headers, body, response_time_ms, success.
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
        body = json.loads(body_json) if body_json else None
    except json.JSONDecodeError as e:
        return _json({"error": f"Invalid JSON: {e}", "success": False})

    send_body = body and method.upper() in ("POST", "PUT", "PATCH")

    try:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
        start = time.perf_counter()
        response = _retry_with_backoff(
            client.request, method.upper(), url, headers=headers,
            json=body if send_body else None,
        )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        client.close()

        try:
            resp_body = response.json()
        except Exception:
            resp_body = response.text

        return _json({
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": resp_body,
            "response_time_ms": elapsed_ms,
            "success": 200 <= response.status_code < 300,
        })
    except httpx.ConnectError as e:
        return _json({
            "status_code": 0, "headers": {},
            "body": f"Connection error: {e}",
            "response_time_ms": 0, "success": False,
        })
    except httpx.TimeoutException:
        return _json({
            "status_code": 0, "headers": {},
            "body": "Request timed out",
            "response_time_ms": timeout * 1000, "success": False,
        })
    except Exception as e:
        return _json({
            "status_code": 0, "headers": {},
            "body": f"Error: {e}",
            "response_time_ms": 0, "success": False,
        })


@function_tool
def validate_response(
    response_json: str,
    expected_status: int = 200,
    required_fields_json: str = "[]",
    field_types_json: str = "{}",
) -> str:
    """Validate an API response against criteria.

    Args:
        response_json: JSON string of response from send_request.
        expected_status: Expected HTTP status code.
        required_fields_json: JSON array of required field names.
        field_types_json: JSON object mapping fields to expected type names.

    Returns:
        JSON string with passed, checks, violations.
    """
    try:
        response = json.loads(response_json)
        required_fields = json.loads(required_fields_json) if required_fields_json else []
        field_types = json.loads(field_types_json) if field_types_json else {}
    except json.JSONDecodeError as e:
        return _json({"error": f"Invalid JSON: {e}", "passed": False})

    checks: list[dict[str, Any]] = []
    violations: list[str] = []

    status_ok = response.get("status_code") == expected_status
    checks.append({
        "check": "status_code",
        "expected": expected_status,
        "actual": response.get("status_code"),
        "passed": status_ok,
    })
    if not status_ok:
        violations.append(
            f"Expected status {expected_status}, got {response.get('status_code')}"
        )

    body = response.get("body", {})
    if isinstance(body, str):
        checks.append({"check": "body_is_json", "passed": False})
        violations.append("Response body is not valid JSON")
        return _json({"passed": len(violations) == 0, "checks": checks, "violations": violations})

    for field in required_fields:
        present = field in body
        checks.append({"check": f"field_{field}", "passed": present})
        if not present:
            violations.append(f"Missing required field: {field}")

    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "array": list, "object": dict,
    }
    for field, expected_type_name in field_types.items():
        if field not in body:
            continue
        expected_type = type_map.get(expected_type_name)
        if expected_type:
            actual = body[field]
            if expected_type_name == "integer" and isinstance(actual, bool):
                type_ok = False
            else:
                type_ok = isinstance(actual, expected_type)
            checks.append({
                "check": f"type_{field}",
                "expected": expected_type_name,
                "actual": type(actual).__name__,
                "passed": type_ok,
            })
            if not type_ok:
                violations.append(
                    f"Field '{field}': expected {expected_type_name}, got {type(actual).__name__}"
                )

    return _json({"passed": len(violations) == 0, "checks": checks, "violations": violations})


@function_tool
def check_data_type(
    value_str: str,
    expected_type: str,
) -> str:
    """Validate the data type of a value.

    Args:
        value_str: JSON string of the value to check.
        expected_type: Expected type (string, integer, boolean, array, object, number).

    Returns:
        JSON string with is_valid, actual_type, expected_type.
    """
    try:
        value = json.loads(value_str)
    except json.JSONDecodeError:
        value = value_str

    type_map = {
        "string": str, "integer": int, "number": (int, float),
        "boolean": bool, "array": list, "object": dict,
    }
    actual_type = type(value).__name__
    expected = type_map.get(expected_type)

    if expected is None:
        return _json({"is_valid": False, "actual_type": actual_type, "expected_type": expected_type})

    if expected_type == "integer" and isinstance(value, bool):
        is_valid = False
    else:
        is_valid = isinstance(value, expected)

    return _json({"is_valid": is_valid, "actual_type": actual_type, "expected_type": expected_type})


@function_tool
def extract_data(
    response_json: str,
    path: str,
) -> str:
    """Extract a specific value from a response using dot-notation path.

    Args:
        response_json: JSON string of API response.
        path: Dot notation path (e.g. 'data.user.id').

    Returns:
        JSON string with value, found, path.
    """
    try:
        response = json.loads(response_json)
    except json.JSONDecodeError:
        return _json({"value": None, "found": False, "path": path})

    body = response.get("body", {})
    if isinstance(body, str):
        return _json({"value": None, "found": False, "path": path})

    keys = path.split(".")
    current: Any = body
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return _json({"value": None, "found": False, "path": path})

    return _json({"value": current, "found": True, "path": path})


@function_tool
def discover_endpoints(
    base_url: str,
    doc_paths_json: str = '["/openapi.json", "/swagger.json", "/docs", "/api-docs"]',
) -> str:
    """Discover API endpoints by fetching OpenAPI/Swagger specs from common paths.

    Args:
        base_url: Base API URL (e.g. https://api.example.com/v1).
        doc_paths_json: JSON array of paths to try for API docs.

    Returns:
        JSON string with discovered endpoints, methods, and auth requirements.
    """
    try:
        doc_paths = json.loads(doc_paths_json) if doc_paths_json else [
            "/openapi.json", "/swagger.json", "/docs", "/api-docs"
        ]
    except json.JSONDecodeError as e:
        return _json({"error": str(e)})

    # Strip trailing slash from base_url for clean joining
    base = base_url.rstrip("/")

    endpoints: list[dict[str, Any]] = []
    methods: dict[str, list[str]] = {}
    auth_required = False
    spec_found = False

    for path in doc_paths:
        url = base + path if not path.startswith("http") else path
        try:
            client = httpx.Client(timeout=10.0, follow_redirects=True)
            resp = client.request("GET", url)
            client.close()

            if resp.status_code != 200:
                continue

            try:
                spec = resp.json()
            except Exception:
                continue

            # OpenAPI 3.x / Swagger 2.0
            paths_obj = spec.get("paths", {})
            if not paths_obj:
                continue

            spec_found = True
            security_schemes = spec.get("components", {}).get("securitySchemes", {}) or spec.get("securityDefinitions", {})
            auth_required = len(security_schemes) > 0

            for endpoint_path, methods_obj in paths_obj.items():
                if not isinstance(methods_obj, dict):
                    continue
                found_methods = []
                for http_method in ("get", "post", "put", "delete", "patch", "head", "options"):
                    if http_method in methods_obj:
                        found_methods.append(http_method.upper())
                if found_methods:
                    full_url = base + endpoint_path
                    endpoints.append({"path": endpoint_path, "url": full_url, "methods": found_methods})
                    methods[endpoint_path] = found_methods

            if spec_found:
                break  # Found a valid spec, stop searching

        except Exception:
            continue

    if not spec_found:
        # Fallback: try the base URL itself as an endpoint
        return _json({
            "endpoints": [{"path": "/", "url": base, "methods": ["GET"]}],
            "methods": {"/": ["GET"]},
            "auth_required": False,
            "spec_found": False,
            "note": "No OpenAPI/Swagger spec found. Test base URL manually.",
        })

    return _json({
        "endpoints": endpoints,
        "methods": methods,
        "auth_required": auth_required,
        "spec_found": True,
        "total_endpoints": len(endpoints),
    })
