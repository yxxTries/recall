"""Slow tests run only when asked: integration tests (real windows, audio, Node) with --integration,
cloud tests (the deployed Supabase project and Groq quota) with --cloud."""
import pytest

GATES = {"integration": "drives real windows, audio and Node (run at every gate)",
         "cloud": "uses the deployed cloud and spends Groq quota (run at the M7 gate)"}


def pytest_addoption(parser):
    for name, help in GATES.items():
        parser.addoption(f"--{name}", action="store_true", help=f"also run the tests that {help}")


def pytest_collection_modifyitems(config, items):
    for name in GATES:
        if config.getoption(name):
            continue
        skip = pytest.mark.skip(reason=f"{name} test: run with --{name}")
        for item in items:
            if name in item.keywords:
                item.add_marker(skip)
