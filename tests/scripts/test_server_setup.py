"""Smoke tests for ``scripts/server-setup.sh``.

The script prepares a server checkout (security env vars + worker unit). It must be safe
to run as a dry-run: no writes, no sudo prompt. Everything here works in a temp copy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "server-setup.sh"
TLS_UNIT = "ielts-reading-studio-tls"
WEB_UNIT = "ielts-reading-studio"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="server-setup.sh requires bash"
)


def _checkout(root: Path, env_file: str | None) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "deploy").mkdir(parents=True)
    shutil.copy(SETUP_SCRIPT, root / "scripts" / "server-setup.sh")
    for name in (
        "ielts-reading-studio-worker.service",
        "ielts-reading-studio.service",
        "ielts-reading-studio-tls.service",
        "Caddyfile",
    ):
        shutil.copy(REPO_ROOT / "deploy" / name, root / "deploy" / name)
    if env_file is not None:
        (root / ".env.web").write_text(env_file, encoding="utf-8")
    return root


def _complete_env() -> str:
    return (
        "IELTS_WEB_USERNAME=reader\n"
        "IELTS_WEB_PASSWORD=super-secret-password\n"
        "IELTS_WEB_SESSION_SECRET=" + "a" * 64 + "\n"
        "IELTS_WEB_FORCE_HTTPS=1\n"
        "IELTS_WEB_TRUSTED_PROXIES=127.0.0.1\n"
    )


def _sudo_shim(root: Path) -> Path:
    """A ``sudo`` that runs real commands and no-ops the ones this host lacks.

    ``--apply`` shells out to ``sudo install`` / ``sudo systemctl``; neither systemd nor a
    ``caddy`` user exists on a developer machine, so the shim keeps the apply path testable
    without pretending the host is a server.
    """

    shim_dir = root / "shim"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "sudo"
    shim.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ] && [ \"${1#-}\" != \"$1\" ]; do shift; done\n"
        "[ \"$#\" -gt 0 ] || exit 0\n"
        "if command -v \"$1\" >/dev/null 2>&1; then exec \"$@\"; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim_dir


_SUDO_SHIM = '''#!{python}
"""Test stand-in for sudo: never escalates and never touches the service manager.

``--apply`` shells out to ``sudo install`` / ``sudo systemctl``. Tests must behave the same
on a laptop (no systemd, no caddy user) and on a server (both exist), so:

* file operations run for real, with ownership flags dropped - a test user cannot chown;
* everything else (``systemctl``, ``chown``, ``journalctl``) is recorded and skipped,
  because on a real server those calls would need root and fail.
"""

import os
import subprocess
import sys

ALLOWED = {{"install", "cp", "mkdir", "chmod", "ln", "rm"}}
DROP_WITH_VALUE = {{"-o", "-g", "-O", "--owner", "--group"}}

args = [item for item in sys.argv[1:] if item != "--"]
if not args:
    raise SystemExit(0)
command, rest = args[0], args[1:]

log = os.environ.get("IELTS_SHIM_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\\n")

if command not in ALLOWED:
    raise SystemExit(0)

cleaned: list[str] = []
skip_next = False
for item in rest:
    if skip_next:
        skip_next = False
        continue
    if item in DROP_WITH_VALUE:
        skip_next = True
        continue
    cleaned.append(item)
raise SystemExit(subprocess.call([command, *cleaned]))
'''

_CADDY_SHIM = '''#!{python}
"""Test stand-in for the caddy binary: only its presence is probed."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "version":
    print("v2.11.4 (test stub)")
raise SystemExit(0)
'''


def _shim_dir(checkout: Path) -> Path:
    """A PATH directory holding the ``sudo`` and ``caddy`` stand-ins.

    ``--apply`` shells out to ``sudo install`` / ``sudo systemctl``, and the script refuses
    to prepare an HTTPS front when ``caddy`` is absent. The stand-ins make the apply path
    testable identically on a developer machine and on a real server; set
    ``IELTS_SHIM_LOG`` to assert which privileged commands the script requested.
    """

    shim_dir = checkout / "shim"
    shim_dir.mkdir(exist_ok=True)
    interpreter = sys.executable or "python3"
    for name, body in (("sudo", _SUDO_SHIM), ("caddy", _CADDY_SHIM)):
        path = shim_dir / name
        path.write_text(body.format(python=interpreter), encoding="utf-8")
        path.chmod(0o755)
    return shim_dir


def _run(checkout: Path, *args: str, systemd_dir: Path | None = None,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    target_dir = systemd_dir or (checkout / "systemd")
    # /etc/systemd/system always exists on a real host; --apply writes units into it.
    target_dir.mkdir(parents=True, exist_ok=True)
    shim_dir = _shim_dir(checkout)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{shim_dir}:{environment.get('PATH', '')}",
            "IELTS_SYSTEMD_DIR": str(target_dir),
            # Never let a test touch /etc/caddy on the machine running it.
            "IELTS_CADDY_CONFIG_DIR": str(checkout / "caddy"),
            "IELTS_SUDO": "sudo" if "--apply" in args else "sudo-should-not-run",
        }
    )
    if extra_env:
        environment.update(extra_env)
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
    assert "--no-tls" in result.stdout


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


def test_dry_run_reports_the_https_entry_without_writing(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())

    result = _run(checkout, "--no-worker")

    assert result.returncode == 0, result.stderr
    assert "HTTPS 入口" in result.stdout
    assert "未安装" in result.stdout
    assert "站点地址" in result.stdout
    assert not (tmp_path / "caddy").exists(), "dry-run 不得写入 Caddy 配置"


def test_apply_renders_the_caddyfile_with_the_configured_site(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())

    result = _run(
        checkout,
        "--apply",
        "--no-worker",
        extra_env={"IELTS_TLS_SITE": "https://203.0.113.9:9443"},
    )

    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "caddy" / "ielts-reading-studio.caddyfile").read_text(encoding="utf-8")
    assert "{{SITE_ADDRESS}}" not in rendered, "占位符必须被替换"
    assert "{{CATCH_ALL_ADDRESS}}" not in rendered
    assert "https://203.0.113.9:9443 {" in rendered
    assert "tls internal" in rendered
    assert "reverse_proxy 127.0.0.1:8768" in rendered
    assert rendered.count("import ielts_site") == 2, "主站点与兜底站点共用同一段配置"
    assert "\n:9443 {" in rendered, "无 SNI 的握手需要同端口兜底站点"
    assert "auto_https disable_redirects" in rendered, "80 端口属于别的服务，不能绑定"


def test_catch_all_falls_back_to_443_without_an_explicit_port(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())

    result = _run(
        checkout,
        "--apply",
        "--no-worker",
        extra_env={"IELTS_TLS_SITE": "https://ielts.example.com"},
    )

    assert result.returncode == 0, result.stderr
    rendered = (tmp_path / "caddy" / "ielts-reading-studio.caddyfile").read_text(encoding="utf-8")
    assert "https://ielts.example.com {" in rendered
    assert "\n:443 {" in rendered


def test_apply_installs_the_https_unit(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    shim_log = tmp_path / "shim.log"

    result = _run(
        checkout, "--apply", "--no-worker", extra_env={"IELTS_SHIM_LOG": str(shim_log)}
    )

    assert result.returncode == 0, result.stderr
    assert (systemd_dir / "ielts-reading-studio-tls.service").exists()
    assert "caddy run" in (systemd_dir / "ielts-reading-studio-tls.service").read_text(
        encoding="utf-8"
    )
    # The privileged commands must be requested even though the stand-in skips them.
    requested = shim_log.read_text(encoding="utf-8")
    assert "systemctl daemon-reload" in requested
    assert f"systemctl enable {TLS_UNIT}" in requested
    assert f"systemctl restart {TLS_UNIT}" in requested


def test_apply_refreshes_a_stale_https_unit_and_config(tmp_path: Path) -> None:
    """A unit that failed to start must be replaced on the next --apply, not skipped.

    This is the regression that broke a real deployment: the unit file existed, so a
    presence-only check left the broken version in place forever.
    """

    checkout = _checkout(tmp_path, _complete_env())
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    installed = systemd_dir / f"{TLS_UNIT}.service"
    installed.write_text("[Service]\nExecStart=/usr/bin/caddy run --data-dir oops\n")
    caddy_dir = tmp_path / "caddy"
    caddy_dir.mkdir()
    (caddy_dir / "ielts-reading-studio.caddyfile").write_text("# 旧配置\n")
    shim_log = tmp_path / "shim.log"

    result = _run(
        checkout, "--apply", "--no-worker", extra_env={"IELTS_SHIM_LOG": str(shim_log)}
    )

    assert result.returncode == 0, result.stderr
    assert "--data-dir" not in installed.read_text(encoding="utf-8")
    rendered = (caddy_dir / "ielts-reading-studio.caddyfile").read_text(encoding="utf-8")
    assert "旧配置" not in rendered
    assert f"systemctl restart {TLS_UNIT}" in shim_log.read_text(encoding="utf-8")


def test_second_apply_leaves_the_https_entry_alone(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    _run(checkout, "--apply", "--no-worker")
    shim_log = tmp_path / "shim.log"
    shim_log.write_text("", encoding="utf-8")
    second = _run(
        checkout, "--apply", "--no-worker", extra_env={"IELTS_SHIM_LOG": str(shim_log)}
    )

    assert second.returncode == 0, second.stderr
    assert "已是最新" in second.stdout
    assert f"systemctl restart {TLS_UNIT}" not in shim_log.read_text(encoding="utf-8")


def test_apply_replaces_an_outdated_web_unit(tmp_path: Path) -> None:
    """The deployed unit used to bind 0.0.0.0:8766, which the TLS front now owns."""

    checkout = _checkout(tmp_path, _complete_env())
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    installed = systemd_dir / "ielts-reading-studio.service"
    installed.write_text(
        "[Service]\nExecStart=/bin/true serve --host 0.0.0.0 --port 8766\n", encoding="utf-8"
    )

    result = _run(checkout, "--apply", "--no-worker", "--no-tls")

    assert result.returncode == 0, result.stderr
    assert "与本仓库版本不同" in result.stdout
    assert "--host 127.0.0.1 --port 8768" in installed.read_text(encoding="utf-8")


def test_upstream_port_drift_is_reported(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())
    unit = checkout / "deploy" / "ielts-reading-studio.service"
    unit.write_text(unit.read_text(encoding="utf-8").replace("--port 8768", "--port 9999"))

    result = _run(checkout, "--no-worker")

    assert result.returncode == 1
    assert "不一致" in result.stderr


def test_tls_handling_can_be_skipped(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path, _complete_env())

    result = _run(checkout, "--no-worker", "--no-tls")

    assert result.returncode == 0, result.stderr
    assert "跳过 HTTPS 入口" in result.stdout or "HTTPS 入口" not in result.stdout


def test_repository_web_unit_port_matches_the_caddy_upstream() -> None:
    """Drift here means Caddy answers 502 while the application looks healthy."""

    upstream = re.search(
        r"reverse_proxy\s+127\.0\.0\.1:(\d+)",
        (REPO_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8"),
    )
    unit = re.search(
        r"--port (\d+)",
        (REPO_ROOT / "deploy" / "ielts-reading-studio.service").read_text(encoding="utf-8"),
    )

    assert upstream is not None, "Caddyfile 必须包含反代目标"
    assert unit is not None, "web 单元必须声明 --port"
    assert upstream.group(1) == unit.group(1)

