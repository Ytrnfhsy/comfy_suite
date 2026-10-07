"""Runs the plugin's JavaScript unit tests (node --test) when node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_plugin_js_units():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, "--test", str(Path(__file__).with_name("unit.test.mjs"))], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
