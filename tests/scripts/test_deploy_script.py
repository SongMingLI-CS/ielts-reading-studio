"""Smoke tests for ``scripts/deploy.sh``.

They exercise everything the script can do without systemd: syntax, ``--help``, the
read-only ``--check`` preflight and ``--dry-run``. Each test builds a throwaway git
checkout in a temp directory and points the script at this interpreter, so the host
service, database and data directories are never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="deploy.sh requires bash and git",
)


def _script(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["IELTS_PYTHON"] = sys.executable
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "deploy.sh"), *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
        check=False,
    )


def _bare_checkout(root: Path) -> Path:
    (root / "scripts").mkdir(parents=True)
    shutil.copy(DEPLOY_SCRIPT, root / "scripts" / "deploy.sh")
    return root


def _prepared_checkout(root: Path) -> Path:
    _bare_checkout(root)
    (root / "config.yaml").write_text(
        "output_dir: output\ndatabase_path: output/state.db\n", encoding="utf-8"
    )
    # A publicly deployable EnvironmentFile: the preflight re-runs the application's own
    # gate, so a missing session secret or a missing HTTPS declaration fails the check.
    (root / ".env.web").write_text(
        "IELTS_WEB_USERNAME=reader\n"
        "IELTS_WEB_PASSWORD=placeholder-not-a-real-secret\n"
        "IELTS_WEB_SESSION_SECRET=" + "b" * 64 + "\n"
        "IELTS_WEB_FORCE_HTTPS=1\n"
        "IELTS_WEB_TRUSTED_PROXIES=127.0.0.1\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("output/\ninput/\nbackups/\n", encoding="utf-8")
    (root / "input").mkdir(exist_ok=True)
    (root / "output").mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


def _head(checkout: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_deploy_script_has_valid_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_help_documents_every_mode(tmp_path: Path) -> None:
    result = _script(_bare_checkout(tmp_path), "--help")

    assert result.returncode == 0, result.stderr
    for flag in ("--check", "--dry-run", "--no-pull", "--skip-tests"):
        assert flag in result.stdout


def test_unknown_option_fails_fast(tmp_path: Path) -> None:
    result = _script(_bare_checkout(tmp_path), "--nonsense")

    assert result.returncode == 1
    assert "未知参数" in result.stderr


def test_check_reports_missing_pieces_without_touching_anything(tmp_path: Path) -> None:
    checkout = _bare_checkout(tmp_path)

    result = _script(checkout, "--check")

    assert result.returncode == 1
    assert "配置文件缺失" in result.stderr
    assert "部署前检查未通过" in result.stderr
    assert not (checkout / "output").exists()
    assert not (checkout / "backups").exists()


def test_check_passes_on_a_prepared_checkout_and_stays_read_only(tmp_path: Path) -> None:
    checkout = _prepared_checkout(tmp_path)

    result = _script(checkout, "--check")

    assert result.returncode == 0, result.stderr
    assert "部署前检查通过" in result.stdout
    assert "检查模式" in result.stdout
    assert "迁移状态" in result.stdout
    # --check must not create the database file nor any backup directory.
    assert not (checkout / "output" / "state.db").exists()
    assert not (checkout / "backups").exists()


def test_dry_run_prints_every_step_and_changes_nothing(tmp_path: Path) -> None:
    checkout = _prepared_checkout(tmp_path)
    before = _head(checkout)

    result = _script(checkout, "--dry-run", "--no-pull")

    assert result.returncode == 0, result.stderr
    assert "[dry-run]" in result.stdout
    assert "[dry-run] sudo systemctl restart" in result.stdout
    assert _head(checkout) == before
    assert not (checkout / "backups").exists()
    assert not (checkout / "output" / "state.db").exists()
    assert "同步依赖" in result.stdout
    assert "执行数据库迁移" in result.stdout


def test_check_refuses_a_configuration_that_fails_the_public_gate(tmp_path: Path) -> None:
    """Behind the TLS front the app binds loopback, so `serve` never runs its own gate.

    Without this check a missing IELTS_WEB_SESSION_SECRET would deploy silently and every
    session would be signed with the development key.
    """

    checkout = _bare_checkout(tmp_path)
    (checkout / "config.yaml").write_text(
        "output_dir: output\ndatabase_path: output/state.db\n", encoding="utf-8"
    )
    (checkout / ".env.web").write_text(
        "IELTS_WEB_USERNAME=reader\nIELTS_WEB_PASSWORD=placeholder-not-a-real-secret\n",
        encoding="utf-8",
    )

    result = _script(checkout, "--check")

    assert result.returncode == 1
    assert "安全配置不满足公网部署要求" in result.stderr
    assert "IELTS_WEB_SESSION_SECRET" in result.stderr
    assert "IELTS_WEB_FORCE_HTTPS" in result.stderr


def test_prepared_checkout_passes_the_public_gate(tmp_path: Path) -> None:
    result = _script(_prepared_checkout(tmp_path), "--check")

    assert result.returncode == 0, result.stderr
    assert "满足公网闸门" in result.stdout


def test_dry_run_documents_both_health_checks(tmp_path: Path) -> None:
    result = _script(_prepared_checkout(tmp_path), "--dry-run", "--no-pull")

    assert result.returncode == 0, result.stderr
    assert "健康检查" in result.stdout
    assert "代理语义检查" in result.stdout, "/healthz 是公开路径，不足以证明站点可用"
