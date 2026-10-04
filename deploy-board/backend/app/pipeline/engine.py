import logging
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import crypto, db
from ..config import settings
from ..db import models as m
from ..db import repositories as repo
from ..events import bus
from ..runners import get_runner
from ..runners.base import EXIT_TIMEOUT, env_file, host_env, stream_process
from .definition import DEFAULT_PIPELINE, DEFAULT_RETRIES, ROLLBACK_PIPELINE

log = logging.getLogger("deployboard.engine")


class StageFailed(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code

    @property
    def retryable(self) -> bool:
        return self.exit_code != EXIT_TIMEOUT  # a timed-out stage would just time out again


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-") or "app"


def container_name(project: m.Project, env: m.Environment) -> str:
    return f"deployboard-{slug(project.name)}-{env.name}"


def plan_stages(project: m.Project, trigger: str) -> list[dict]:
    if trigger == "rollback":
        return ROLLBACK_PIPELINE
    return project.pipeline or DEFAULT_PIPELINE


class Execution:
    """Runs one claimed (RUNNING) deployment: walks the stages, records status and logs, streams events,
    and guarantees that every stage and the deployment end in a final state."""

    def __init__(self, deployment_id: int, runner=None):
        self.s = db.new_session()
        self.dep = repo.get_deployment(self.s, deployment_id)
        self.project = self.dep.project
        self.environment = self.dep.environment
        self.runner = runner or get_runner()
        self.current_stage = ""
        self.secrets: list[str] = []
        self.env: dict[str, str] = {}
        for v in self.environment.variables:
            val = crypto.decrypt(v.value) if v.is_secret else v.value
            self.env[v.key] = val
            if v.is_secret and len(val) >= 4:
                self.secrets.append(val)
        self.env.update(DEPLOYBOARD_PROJECT=self.project.name, DEPLOYBOARD_ENVIRONMENT=self.environment.name,
                        DEPLOYBOARD_DEPLOYMENT=str(self.dep.id))
        self.workdir = settings.workspace_dir / slug(self.project.name) / f"{self.environment.name}-{self.dep.id}"
        self.deadline = time.monotonic() + settings.deployment_timeout
        self._heartbeat_stop = threading.Event()

    # ---- logging / events -------------------------------------------------
    def log(self, line: str, stage: str | None = None) -> None:
        for sec in self.secrets:
            line = line.replace(sec, "***")
        stage = self.current_stage if stage is None else stage
        row = m.LogLine(deployment_id=self.dep.id, stage=stage, line=line)
        self.s.add(row)
        self.s.commit()
        bus.publish(self.dep.id, {"type": "log", "id": row.id, "stage": stage, "line": line, "ts": row.ts.isoformat()})

    def _emit_stage(self, st: m.Stage) -> None:
        bus.publish(self.dep.id, {"type": "stage", "name": st.name, "status": st.status, "exit_code": st.exit_code})

    def _emit_deployment(self) -> None:
        bus.publish(self.dep.id, {"type": "deployment", "status": self.dep.status, "image": self.dep.image})

    # ---- lease heartbeat ----------------------------------------------------
    def _heartbeat(self) -> None:
        interval = max(0.2, settings.lease_seconds / 3)
        while not self._heartbeat_stop.wait(interval):
            try:
                with db.new_session() as s:
                    repo.renew_lease(s, self.dep.id, repo.lease_for(settings.lease_seconds, m.now()))
            except Exception:
                log.warning("could not renew lease for deployment %s", self.dep.id, exc_info=True)

    # ---- main loop --------------------------------------------------------
    def run(self) -> str:
        dep = self.dep
        hb = threading.Thread(target=self._heartbeat, name=f"lease-{dep.id}", daemon=True)
        hb.start()
        failed = False
        try:
            defs = plan_stages(self.project, dep.trigger)
            if not dep.stages:
                for i, d in enumerate(defs):
                    self.s.add(m.Stage(deployment_id=dep.id, position=i, name=d["name"]))
                self.s.commit()
                self.s.refresh(dep)
            self._emit_deployment()
            if dep.attempts > 1:
                self.log(f"↻ attempt {dep.attempts}: previous worker was lost", "")
            stages = {st.name: st for st in dep.stages}
            for d in defs:
                st = stages[d["name"]]
                if failed:
                    st.status = "SKIPPED"
                    self.s.commit()
                    self._emit_stage(st)
                elif not self._run_stage(d, st):
                    failed = True
        except Exception as e:  # engine-level bug: record it, never leave the deployment RUNNING
            log.exception("deployment %s: engine error", dep.id)
            failed = True
            dep.error = f"internal error: {type(e).__name__}: {e}"
            self._settle_stages()
            self.log(dep.error, "")
        finally:
            self._heartbeat_stop.set()
            hb.join(timeout=2)
        self._cleanup()  # before the final status becomes visible, so "finished" implies "workspace gone"
        dep.status = "FAILED" if failed else "SUCCESS"
        dep.finished_at, dep.lease_expires_at = m.now(), None
        self.s.commit()
        self._emit_deployment()
        self.s.close()
        return dep.status

    def _settle_stages(self) -> None:
        """After an engine-level error: close whatever is still open."""
        for st in self.dep.stages:
            if st.status == "RUNNING":
                st.status, st.finished_at = "FAILED", m.now()
            elif st.status == "PENDING":
                st.status = "SKIPPED"
        self.s.commit()

    def _cleanup(self) -> None:
        if not settings.keep_workspace:
            shutil.rmtree(self.workdir, ignore_errors=True)

    def _run_stage(self, d: dict, st: m.Stage) -> bool:
        self.current_stage = st.name
        st.status, st.started_at, st.exit_code, st.attempts = "RUNNING", m.now(), None, 0
        self.s.commit()
        self._emit_stage(st)
        retries = int(d.get("retries", DEFAULT_RETRIES.get(d["kind"], 0)))
        delay = float(d.get("retry_delay", 2))
        timeout = float(d.get("timeout", settings.stage_timeout))
        handler = getattr(self, f"_do_{d['kind']}")
        error: StageFailed | None = None
        for attempt in range(1, retries + 2):
            st.attempts = attempt
            self.s.commit()
            self.log(f"▶ {st.name}" + (f" (attempt {attempt}/{retries + 1})" if attempt > 1 else ""))
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                error = StageFailed(f"deployment exceeded its {settings.deployment_timeout}s time limit", EXIT_TIMEOUT)
                break
            try:
                handler(d, min(timeout, remaining))
                error = None
                break
            except StageFailed as e:
                error = e
            except Exception as e:  # unexpected error inside a stage handler
                log.exception("deployment %s stage %s: unexpected error", self.dep.id, st.name)
                error = StageFailed(f"internal error: {type(e).__name__}: {e}")
                break
            if attempt > retries or not error.retryable:
                break
            self.log(f"✕ {error}")
            self.log(f"retrying in {delay:g}s")
            time.sleep(delay)
        if error:
            st.status, st.exit_code = "FAILED", error.exit_code
            self.dep.error = f"stage {st.name!r} failed: {error}"
            self.log(f"✕ {error}")
        else:
            st.status, st.exit_code = "SUCCESS", 0
        st.finished_at = m.now()
        self.s.commit()
        self._emit_stage(st)
        return error is None

    # ---- helpers ------------------------------------------------------------
    def _host(self, argv: list[str], timeout: float, quiet: bool = False) -> int:
        """Run a command on the worker host (git, docker CLI)."""
        return stream_process(argv, cwd=self.workdir if self.workdir.exists() else None, env=host_env({}),
                              on_line=(lambda _: None) if quiet else self.log, timeout=timeout)

    def _capture(self, argv: list[str]) -> str:
        out: list[str] = []
        stream_process(argv, cwd=self.workdir, env=host_env({}), on_line=out.append, timeout=30)
        return "\n".join(out).strip()

    def _image_tag(self) -> str:
        return f"{slug(self.project.name)}-{self.environment.name}:{(self.dep.commit or f'd{self.dep.id}')[:7]}"

    # ---- stage kinds --------------------------------------------------------
    def _do_checkout(self, d: dict, timeout: float) -> None:
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.parent.mkdir(parents=True, exist_ok=True)
        branch = self.dep.branch or self.environment.branch
        code = self._host(["git", "clone", "--quiet", "--branch", branch, self.project.repo_url, str(self.workdir)], timeout)
        if code:
            raise StageFailed(f"git clone exited with code {code}", code)
        if self.dep.commit:
            code = self._host(["git", "checkout", "--quiet", self.dep.commit], timeout)
            if code:
                raise StageFailed(f"git checkout {self.dep.commit} exited with code {code}", code)
        self.dep.commit = self._capture(["git", "rev-parse", "HEAD"]) or self.dep.commit
        self.dep.author = self.dep.author or self._capture(["git", "log", "-1", "--format=%an"])
        self.s.commit()
        self.log(f"checked out {self.dep.commit[:7]} on {branch}")

    def _do_shell(self, d: dict, timeout: float) -> None:
        if not self.workdir.exists():
            raise StageFailed("no checkout in the workspace (put a `checkout` stage first)")
        self.log(f"$ {d['run']}")
        code = self.runner.run(d["run"], cwd=self.workdir, env=self.env, on_line=self.log, timeout=timeout)
        if code:
            raise StageFailed(f"process exited with code {code}", code)

    def _do_docker_build(self, d: dict, timeout: float) -> None:
        if not (self.workdir / "Dockerfile").exists():
            raise StageFailed("no Dockerfile in the repository root")
        tag = self._image_tag()
        self.log(f"$ docker build -t {tag} .")
        code = self._host(["docker", "build", "-t", tag, "--label", f"deployboard.project={self.project.name}",
                           "--label", f"deployboard.environment={self.environment.name}", "."], timeout)
        if code:
            raise StageFailed(f"docker build exited with code {code}", code)
        self.dep.image = tag
        self.s.commit()

    def _do_docker_run(self, d: dict, timeout: float) -> None:
        if not self.dep.image:
            raise StageFailed("no image to deploy (a docker_build stage must come first)")
        name = container_name(self.project, self.environment)
        port = self.environment.port
        argv = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
                "--label", "deployboard.managed=true", "--label", f"deployboard.project={self.project.name}",
                "--label", f"deployboard.environment={self.environment.name}", "--label", f"deployboard.deployment={self.dep.id}"]
        if port:
            argv += ["-p", f"{port}:{port}"]
        if settings.docker_network:
            argv += ["--network", settings.docker_network]
        self._host(["docker", "rm", "-f", name], 60, quiet=True)  # replace the previous version of this environment
        try:
            with env_file({k: v for k, v in self.env.items() if k != "RUNNER_IMAGE"}) as path:
                argv += ["--env-file", str(path), self.dep.image]
                self.log(f"$ docker run -d --name {name} {self.dep.image}")
                code = self._host(argv, timeout)
        except ValueError as e:
            raise StageFailed(str(e))
        if code:
            raise StageFailed(f"docker run exited with code {code}", code)

    def _do_healthcheck(self, d: dict, timeout: float) -> None:
        url = self.environment.health_url
        if not url:
            raise StageFailed("environment has no health_url configured")
        attempts, interval = int(d.get("attempts", 5)), float(d.get("interval", 2))
        end = time.monotonic() + timeout
        last = ""
        for attempt in range(1, attempts + 1):
            if attempt > 1 or d.get("initial_delay", True):
                time.sleep(min(interval, max(0.0, end - time.monotonic())))
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    if 200 <= r.status < 300:
                        self.log(f"GET {url} -> {r.status} (attempt {attempt})")
                        return
                    last = f"status {r.status}"
            except urllib.error.HTTPError as e:
                last = f"status {e.code}"
            except Exception as e:
                last = type(e).__name__
            self.log(f"GET {url} -> {last} (attempt {attempt}/{attempts})")
            if time.monotonic() >= end:
                last += f"; stage timeout of {timeout:g}s reached"
                break
        if self.dep.image:  # help the user: show what the container printed
            self.log("--- container logs (last 30 lines) ---")
            self._host(["docker", "logs", "--tail", "30", container_name(self.project, self.environment)], 30)
        raise StageFailed(f"health check failed after {attempt} attempts: {last}")
