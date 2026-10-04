import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import crypto, db
from ..config import settings
from ..events import bus
from ..runners import get_runner
from ..runners.base import host_env, stream_process
from .definition import DEFAULT_PIPELINE, ROLLBACK_PIPELINE


class StageFailed(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-") or "app"


def container_name(project: db.Project) -> str:
    return f"deployboard-{slug(project.name)}-{slug(project.environment)}"


def plan_stages(project: db.Project, trigger: str) -> list[dict]:
    if trigger == "rollback":
        return ROLLBACK_PIPELINE
    return project.pipeline or DEFAULT_PIPELINE


class Execution:
    """Executes one deployment: walks the stages, records status/logs, and streams events."""

    def __init__(self, deployment_id: int, runner=None):
        self.deployment_id = deployment_id
        self.runner = runner or get_runner()
        self.s = db.SessionLocal()
        self.dep = self.s.get(db.Deployment, deployment_id)
        self.project = self.s.get(db.Project, self.dep.project_id)
        self.current_stage = ""
        self.env: dict[str, str] = {}
        self.secrets: list[str] = []
        for v in self.project.variables:
            val = crypto.decrypt(v.value) if v.is_secret else v.value
            self.env[v.key] = val
            if v.is_secret and len(val) >= 4:
                self.secrets.append(val)
        self.workdir = settings.workspace_dir / slug(self.project.name) / str(self.dep.id)

    # ---- logging / events -------------------------------------------------
    def log(self, line: str, stage: str | None = None) -> None:
        for sec in self.secrets:
            line = line.replace(sec, "***")
        stage = self.current_stage if stage is None else stage
        row = db.LogLine(deployment_id=self.dep.id, stage=stage, line=line)
        self.s.add(row)
        self.s.commit()
        bus.publish(self.dep.id, {"type": "log", "id": row.id, "stage": stage, "line": line, "ts": row.ts.isoformat()})

    def _emit_stage(self, st: db.Stage) -> None:
        bus.publish(self.dep.id, {"type": "stage", "name": st.name, "status": st.status, "exit_code": st.exit_code})

    def _emit_deployment(self) -> None:
        bus.publish(self.dep.id, {"type": "deployment", "status": self.dep.status, "image": self.dep.image})

    # ---- main loop --------------------------------------------------------
    def run(self) -> str:
        dep = self.dep
        defs = plan_stages(self.project, dep.trigger)
        if not dep.stages:
            for i, d in enumerate(defs):
                self.s.add(db.Stage(deployment_id=dep.id, position=i, name=d["name"]))
        dep.status, dep.started_at = "RUNNING", db.now()
        self.s.commit()
        self.s.refresh(dep)
        self._emit_deployment()
        stages = {st.name: st for st in dep.stages}
        failed = False
        try:
            for d in defs:
                st = stages[d["name"]]
                if failed:
                    st.status = "SKIPPED"
                    self.s.commit()
                    self._emit_stage(st)
                    continue
                failed = not self._run_stage(d, st)
        except Exception as e:  # engine bug / unexpected: never leave a deployment RUNNING
            failed = True
            dep.error = f"internal error: {e}"
            self.log(dep.error, "")
        dep.status = "FAILED" if failed else "SUCCESS"
        dep.finished_at = db.now()
        self.s.commit()
        self._emit_deployment()
        self.s.close()
        return dep.status

    def _run_stage(self, d: dict, st: db.Stage) -> bool:
        self.current_stage = st.name
        st.status, st.started_at = "RUNNING", db.now()
        self.s.commit()
        self._emit_stage(st)
        self.log(f"▶ {st.name}")
        try:
            getattr(self, f"_do_{d['kind']}")(d)
            st.exit_code, st.status = 0, "SUCCESS"
        except StageFailed as e:
            st.exit_code, st.status = e.exit_code, "FAILED"
            self.dep.error = f"stage {st.name!r} failed: {e}"
            self.log(f"✕ {e}")
        st.finished_at = db.now()
        self.s.commit()
        self._emit_stage(st)
        return st.status == "SUCCESS"

    # ---- stage kinds ------------------------------------------------------
    def _sh(self, argv_or_cmd, *, host: bool, timeout: int | None = None) -> int:
        timeout = timeout or settings.stage_timeout
        if host:
            return stream_process(argv_or_cmd, cwd=self.workdir if self.workdir.exists() else None,
                                  env=host_env({}), on_line=self.log, timeout=timeout)
        return self.runner.run(argv_or_cmd, cwd=self.workdir, env=self.env, on_line=self.log, timeout=timeout)

    def _do_checkout(self, d: dict) -> None:
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.parent.mkdir(parents=True, exist_ok=True)
        branch = self.dep.branch or self.project.branch
        code = stream_process(["git", "clone", "--quiet", "--branch", branch, self.project.repo_url, str(self.workdir)],
                              cwd=None, env=host_env({}), on_line=self.log, timeout=settings.stage_timeout)
        if code:
            raise StageFailed(f"git clone exited with code {code}", code)
        if self.dep.commit:
            code = self._sh(["git", "checkout", "--quiet", self.dep.commit], host=True)
            if code:
                raise StageFailed(f"git checkout {self.dep.commit} exited with code {code}", code)
        sha = self._capture(["git", "rev-parse", "HEAD"])
        self.dep.commit = sha or self.dep.commit
        self.dep.author = self.dep.author or self._capture(["git", "log", "-1", "--format=%an"])
        self.s.commit()
        self.log(f"checked out {self.dep.commit[:7]} on {branch}")

    def _capture(self, argv: list[str]) -> str:
        out: list[str] = []
        stream_process(argv, cwd=self.workdir, env=host_env({}), on_line=out.append, timeout=30)
        return "\n".join(out).strip()

    def _do_shell(self, d: dict) -> None:
        self.log(f"$ {d['run']}")
        code = self._sh(d["run"], host=False)
        if code:
            raise StageFailed(f"process exited with code {code}", code)

    def _image_tag(self) -> str:
        return f"{slug(self.project.name)}:{(self.dep.commit or str(self.dep.id))[:7]}"

    def _do_docker_build(self, d: dict) -> None:
        if not (self.workdir / "Dockerfile").exists():
            raise StageFailed("no Dockerfile in repository")
        tag = self._image_tag()
        self.log(f"$ docker build -t {tag} .")
        code = self._sh(["docker", "build", "-t", tag, "."], host=True)
        if code:
            raise StageFailed(f"docker build exited with code {code}", code)
        self.dep.image = tag
        self.s.commit()

    def _do_docker_run(self, d: dict) -> None:
        if not self.dep.image:
            raise StageFailed("no image to deploy")
        port = self.project.port
        name = container_name(self.project)
        self._sh(["docker", "rm", "-f", name], host=True)
        argv = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped"]
        if port:
            argv += ["-p", f"{port}:{port}"]
        for k, v in self.env.items():
            if k != "RUNNER_IMAGE":
                argv += ["-e", f"{k}={v}"]
        argv.append(self.dep.image)
        self.log(f"$ docker run -d --name {name} {self.dep.image}")
        code = self._sh(argv, host=True)
        if code:
            raise StageFailed(f"docker run exited with code {code}", code)

    def _do_healthcheck(self, d: dict) -> None:
        url = self.project.health_url
        if not url:
            raise StageFailed("project has no health_url configured")
        retries, interval = int(d.get("retries", 5)), float(d.get("interval", 2))
        last = ""
        for attempt in range(1, retries + 1):
            time.sleep(interval if attempt > 1 or d.get("initial_delay", True) else 0)
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
            self.log(f"GET {url} -> {last} (attempt {attempt}/{retries})")
        raise StageFailed(f"health check failed after {retries} attempts: {last}")
