"""Fixtures for tests against a real headless PhotoSuite (``photosuite-cli serve``).

Set ``PHOTOSUITE_CLI`` to the binary (or put ``photosuite-cli`` on PATH); the tests that need it
are skipped otherwise.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest


def cli_path() -> str | None:
    return os.environ.get("PHOTOSUITE_CLI") or shutil.which("photosuite-cli")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def photosuite(tmp_path: Path):
    exe = cli_path()
    if not exe:
        pytest.skip("photosuite-cli not available (set PHOTOSUITE_CLI)")
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    token = tmp_path / "token"
    port = free_port()
    proc = subprocess.Popen(
        [exe, "serve", "--port", str(port), "--control-token-file", str(token), "--automation-read-root", str(exchange), "--automation-write-root", str(exchange)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        yield {"port": port, "token_file": token, "exchange": exchange}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
