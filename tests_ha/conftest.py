"""Fixtures for tests on a real Home Assistant.

Needs pytest-homeassistant-custom-component:

    python -m pytest tests_ha
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
