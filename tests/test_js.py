"""Runs the editor's JavaScript tests (tests/js) with Node, so `pytest` covers both."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_editor_model_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js isn't installed")
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([node, "--test", "tests/js"], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
