"""API Testing Agent — AI-powered API testing with OpenAI Agents SDK."""

from .orchestrator import run_full_test_suite

__all__ = ["run_full_test_suite", "main"]


def main():
    """CLI entry point — see `api_tester.cli` for implementation."""
    import asyncio
    from .cli import cli_entry
    asyncio.run(cli_entry())
