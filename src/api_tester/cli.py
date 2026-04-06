"""
CLI entry point for the API Testing Agent.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional

from dotenv import load_dotenv

from .orchestrator import run_full_test_suite


def _load_endpoints_from_file(path: str) -> Optional[list[dict[str, Any]]]:
    """Load endpoints from a JSON file."""
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "endpoints" in data:
            return data["endpoints"]
    except Exception as e:
        print(f"Warning: Could not load endpoints from {path}: {e}", file=sys.stderr)
    return None


def _parse_endpoints_arg(value: str) -> Optional[list[dict[str, Any]]]:
    """Parse endpoints from a JSON string or file path."""
    if value.endswith(".json"):
        return _load_endpoints_from_file(value)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


async def cli_entry() -> None:
    parser = argparse.ArgumentParser(
        description="API Testing Agent — AI-powered API testing with OpenAI Agents SDK",
    )
    parser.add_argument(
        "base_url",
        help="Base URL of the API to test (e.g. https://api.example.com/v1)",
    )
    parser.add_argument(
        "--name", "-n",
        default="Target API",
        help="Name of the API (default: 'Target API')",
    )
    parser.add_argument(
        "--auth-token", "-t",
        default=None,
        help="Authentication token (Bearer token or API key)",
    )
    parser.add_argument(
        "--endpoints", "-e",
        default=None,
        help="Endpoints to test as JSON string, or path to a JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the test report (default: print to stdout)",
    )
    parser.add_argument(
        "--openai-api-key",
        default=None,
        help="OpenAI API key (or set OPENAI_API_KEY env var)",
    )

    args = parser.parse_args()

    # Resolve API key
    api_key = args.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            "Error: OPENAI_API_KEY environment variable is not set.\n"
            "Set it or pass --openai-api-key",
            file=sys.stderr,
        )
        sys.exit(1)

    os.environ["OPENAI_API_KEY"] = api_key
    load_dotenv()

    # Parse endpoints
    endpoints = None
    if args.endpoints:
        endpoints = _parse_endpoints_arg(args.endpoints)
        if endpoints is None:
            print("Warning: Could not parse endpoints. Will auto-discover.", file=sys.stderr)

    print(f"\n{'='*60}")
    print(f"  API Testing Agent — {args.name}")
    print(f"  Base URL: {args.base_url}")
    print(f"{'='*60}\n")

    try:
        report = await run_full_test_suite(
            api_name=args.name,
            base_url=args.base_url,
            auth_token=args.auth_token,
            endpoints=endpoints,
        )

        # Output
        if args.output:
            with open(args.output, "w") as f:
                f.write(report)
            print(f"\nReport saved to: {args.output}")
        else:
            print("\n" + report)

    except Exception as e:
        print(f"\nError running test suite: {e}", file=sys.stderr)
        sys.exit(1)
