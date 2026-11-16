# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""The `fondaco` dispatcher routes each subcommand and nothing else."""

import pytest

from api import cli


def test_no_command_prints_usage_and_fails(capsys):
    assert cli.main([]) == 2
    assert "usage: fondaco" in capsys.readouterr().out


def test_help(capsys):
    assert cli.main(["--help"]) == 0
    assert "discover" in capsys.readouterr().out


def test_unknown_command(capsys):
    assert cli.main(["drop-everything"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_discover_is_routed(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "executor.discovery.cli.main", lambda argv: seen.setdefault("a", argv) and 0
    )
    cli.main(["discover", "--csv", "x.csv"])
    assert seen["a"] == ["discover", "--csv", "x.csv"]


def test_mcp_is_routed(monkeypatch):
    pytest.importorskip("mcp")
    seen = {}
    monkeypatch.setattr("api.mcp_server.main", lambda argv: seen.setdefault("a", argv) and 0)
    cli.main(["mcp", "--transport", "stdio"])
    assert seen["a"] == ["--transport", "stdio"]
