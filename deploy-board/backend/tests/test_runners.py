import os
import stat
import time
from pathlib import Path

import pytest

from app.config import settings
from app.runners.base import EXIT_NOT_FOUND, EXIT_TIMEOUT, env_file, stream_process
from app.runners.docker import DockerRunner
from app.runners.local import LocalRunner


def run(cmd, timeout=10, shell=True, cwd=None, env=None):
    lines: list[str] = []
    code = stream_process(cmd, cwd=cwd, env=env or dict(os.environ), on_line=lines.append, timeout=timeout, shell=shell)
    return code, lines


def test_stream_process_output_and_exit_codes():
    assert run("echo a; echo b >&2; exit 3") == (3, ["a", "b"])
    assert run(["definitely-not-a-command"], shell=False)[0] == EXIT_NOT_FOUND
    code, lines = run("echo hi", cwd=Path("/nonexistent-dir"))
    assert code == 126 and "cannot start" in lines[0]


def test_timeout_kills_whole_process_group():
    t = time.time()
    code, lines = run("sleep 30 & sleep 30", timeout=1)
    assert code == EXIT_TIMEOUT and "timed out" in lines[-1]
    assert time.time() - t < 8  # the grandchild must not keep the pipe (and us) blocked


def test_background_child_does_not_block_after_main_exit():
    t = time.time()
    code, _ = run("sleep 30 & echo done", timeout=20)
    assert code == 0 and time.time() - t < 8


def test_signal_exit_code_is_positive():
    assert run("kill -9 $$")[0] == 137


def test_local_runner_passes_env_and_cwd(tmp_path):
    lines: list[str] = []
    code = LocalRunner().run("echo $FOO; pwd", cwd=tmp_path, env={"FOO": "bar"}, on_line=lines.append, timeout=10)
    assert code == 0 and lines == ["bar", str(tmp_path.resolve())]
    lines.clear()
    LocalRunner().run("echo ${SECRET_FROM_PARENT:-unset}", cwd=tmp_path, env={}, on_line=lines.append, timeout=10)
    assert lines == ["unset"]  # the host's own environment is not leaked into stages


def test_env_file_private_and_removed():
    with env_file({"A": "1", "B": "x=y"}) as p:
        assert p.read_text() == "A=1\nB=x=y\n"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not p.exists()
    with pytest.raises(ValueError):
        with env_file({"A": "multi\nline"}):
            pass


def test_docker_runner_builds_expected_argv(tmp_path, monkeypatch):
    """Static check of the command line only. This does NOT execute Docker."""
    monkeypatch.setattr(settings, "runner_cpus", "1.5")
    monkeypatch.setattr(settings, "runner_memory", "512m")
    argv = DockerRunner().build_argv("npm test", cwd=tmp_path, name="n1", env_path=Path("/tmp/envf"), image="node:22")
    assert argv[:3] == ["docker", "run", "--rm"] and argv[-4:] == ["node:22", "sh", "-c", "npm test"]
    assert f"{tmp_path.resolve()}:/workspace" in argv and "--env-file" in argv and "/tmp/envf" in argv
    assert argv[argv.index("--cpus") + 1] == "1.5" and argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("-u") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert not any(a.startswith("-e") and "SECRET" in a for a in argv)  # secrets travel via --env-file only
