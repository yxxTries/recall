"""Integration tests (real windows, audio, Node) are slow and take the foreground; they run only with --integration."""
import pytest


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true",
                     help="also run the tests that drive real windows, audio and Node (run at every gate)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason="integration test: run with --integration")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
