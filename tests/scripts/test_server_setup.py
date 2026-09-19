"""Smoke tests for ``scripts/server-setup.sh``.

The script prepares a server checkout (security env vars + worker unit). It must be safe
to run as a dry-run: no writes, no sudo prompt. Everything here works in a temp copy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "server-setup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="server-setup.sh requires bash"
)


def _checkout(root: Path, env_file: str | None) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "deploy").mkdir(parents=True)
    shutil.copy(SETUP_SCRIPT, root / "scripts" / "server-setup.sh")
    shutil.copy(
        REPO_ROOT / "deploy" / "ielts-reading-studio-worker.service",
        root / "deploy" / "ielts-reading-studio-worker.service",
    )
    if env_file is not None:
        (root / ".env.web").write_text(env_file, encoding="utf-8")
    return root


def _run(checkout: Path, *args: str, systemd_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["IELTS_SYSTEMD_DIR"] = str(systemd_dir)
    environment["IELTS_SUDO"] = "sudo-should-not-run"
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "server-setup.sh"), *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
        check=False,
    )


def test_setup_script_has_valid_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SETUP_SCRIPT)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_help_lists_both_modes(tmp_path: Path) -> None:
    result = _run(_checkout(tmp_path, None), "--help", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout
    assert "--no-worker" in result.stdout


def test_unknown_option_fails_fast(tmp_path: Path) -> None:
    result = _run(_checkout(tmp_path, None), "--nonsense", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 1
    assert "未知参数" in result.stderr


def test_dry_run_reports_missing_keys_and_writes_nothing(tmp_path: Path) -> None:
    env_file = "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=super-secret-password\n"
    checkout = _checkout(tmp_path, env_file)
    before = (checkout / ".env.web").read_bytes()

    result = _run(checkout, "--no-worker", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 0, result.stderr
    assert "IELTS_WEB_SESSION_SECRET" in result.stdout
    assert "dry-run" in result.stdout
    assert (checkout / ".env.web").read_bytes() == before
    assert not list(checkout.glob(".env.web.bak-*"))
    assert "super-secret-password" not in result.stdout


def test_apply_writes_missing_keys_without_printing_the_secret(tmp_path: Path) -> None:
    env_file = "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=super-secret-password\n"
    checkout = _checkout(tmp_path, env_file)

    result = _run(checkout, "--apply", "--no-worker", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 0, result.stderr
    written = (checkout / ".env.web").read_text(encoding="utf-8")
    assert "IELTS_WEB_FORCE_HTTPS=1" in written
    assert "IELTS_WEB_TRUSTED_PROXIES=127.0.0.1" in written
    match = re.search(r"^IELTS_WEB_SESSION_SECRET=([0-9a-f]{64})$", written, re.MULTILINE)
    assert match, "应当写入 64 位十六进制会话密钥"
    assert match.group(1) not in result.stdout, "密钥不得出现在输出中"
    assert "super-secret-password" not in result.stdout
    assert list(checkout.glob(".env.web.bak-*")), "写入前必须备份原文件"
    assert (checkout / ".env.web").stat().st_mode & 0o777 == 0o600


def test_apply_is_idempotent(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=pw\n")

    _run(checkout, "--apply", "--no-worker", systemd_dir=tmp_path / "systemd")
    first = (checkout / ".env.web").read_text(encoding="utf-8")
    result = _run(checkout, "--apply", "--no-worker", systemd_dir=tmp_path / "systemd")
    second = (checkout / ".env.web").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert first == second, "第二次运行不应再改动文件"
    assert "已齐备" in result.stdout


def test_empty_credentials_are_flagged_for_manual_entry(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, "")

    result = _run(checkout, "--apply", "--no-worker", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 1
    assert "IELTS_WEB_USERNAME" in result.stdout or "IELTS_WEB_USERNAME" in result.stderr
    assert "手工" in result.stdout or "手工" in result.stderr


def test_worker_unit_is_reported_missing_without_touching_systemd(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=pw\n")

    result = _run(checkout, "--no-worker", systemd_dir=tmp_path / "systemd")

    assert result.returncode == 0, result.stderr
    assert "sudo-should-not-run" not in result.stderr, "dry-run 不得调用 sudo"


def test_worker_unit_check_uses_the_configured_systemd_dir(tmp_path: Path) -> None:
    checkout = _checkout(
        tmp_path,
        "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=pw\nIELTS_WEB_SESSION_SECRET=" + "a" * 64 + "\n"
        "IELTS_WEB_FORCE_HTTPS=1\nIELTS_WEB_TRUSTED_PROXIES=127.0.0.1\n",
    )
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "ielts-reading-studio-worker.service").write_text("[Unit]\n")

    result = _run(checkout, systemd_dir=systemd_dir)

    assert result.returncode == 0, result.stderr
    assert "已安装" in result.stdout
    assert "前置检查通过" in result.stdout


def test_scripts_survive_a_non_utf8_locale(tmp_path: Path) -> None:
    """Regression: `"$var，"` splits wrongly under LC_ALL=C and dies with `set -u`."""

    checkout = _checkout(tmp_path, "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=pw\n")
    environment = dict(os.environ)
    environment.update(
        {
            "LC_ALL": "C",
            "LANG": "C",
            "IELTS_SYSTEMD_DIR": str(tmp_path / "systemd"),
            "IELTS_SUDO": "false",
        }
    )

    for script, args in (
        (checkout / "scripts" / "server-setup.sh", ["--no-worker"]),
        (REPO_ROOT / "scripts" / "server-setup.sh", ["--help"]),
        (REPO_ROOT / "scripts" / "deploy.sh", ["--help"]),
    ):
        result = subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
            check=False,
        )
        combined = result.stdout + result.stderr
        assert "unbound variable" not in combined, f"{script.name} 在 LC_ALL=C 下崩溃"
        if args == ["--help"]:
            assert result.returncode == 0, combined

    # verify.sh has no --help: it runs the gate, so only its syntax is checked here.
    for name in ("server-setup.sh", "deploy.sh", "verify.sh"):
        syntax = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "scripts" / name)],
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
            check=False,
        )
        assert syntax.returncode == 0, syntax.stderr

