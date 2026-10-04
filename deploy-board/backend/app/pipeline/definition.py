"""Pipeline definition: an ordered list of stages. Each stage has a `kind`:

  checkout      git clone the environment's branch at the requested commit (built-in)
  shell         run `run` inside the runner (local process or isolated container)
  docker_build  build an image tagged <project>-<environment>:<sha7>
  docker_run    (re)start the environment's container from the image
  healthcheck   GET the environment's health_url until it returns 2xx (`attempts`, `interval`)

Options valid on every stage: `timeout` (seconds), `retries` (extra attempts after a failure, never after a
timeout) and `retry_delay` (seconds). `checkout` retries twice by default (network flakiness)."""
KINDS = {"checkout", "shell", "docker_build", "docker_run", "healthcheck"}
DEFAULT_RETRIES = {"checkout": 2}

DEFAULT_PIPELINE = [
    {"name": "checkout", "kind": "checkout"},
    {"name": "install", "kind": "shell", "run": "npm ci"},
    {"name": "test", "kind": "shell", "run": "npm test"},
    {"name": "build", "kind": "shell", "run": "npm run build"},
    {"name": "docker", "kind": "docker_build"},
    {"name": "deploy", "kind": "docker_run"},
    {"name": "healthcheck", "kind": "healthcheck", "attempts": 5, "interval": 2},
]

ROLLBACK_PIPELINE = [
    {"name": "deploy", "kind": "docker_run"},
    {"name": "healthcheck", "kind": "healthcheck", "attempts": 5, "interval": 2},
]

_NUMERIC = {  # option -> (min, max, integer only)
    "timeout": (1, 86400, True), "retries": (0, 10, True), "retry_delay": (0, 600, False),
    "attempts": (1, 120, True), "interval": (0, 600, False),
}


def validate(stages: list[dict]) -> list[dict]:
    names: set[str] = set()
    for s in stages:
        if not isinstance(s, dict):
            raise ValueError("stage must be an object")
        if s.get("kind") not in KINDS:
            raise ValueError(f"unknown stage kind: {s.get('kind')!r}")
        name = s.get("name")
        if not isinstance(name, str) or not name or len(name) > 50:
            raise ValueError("stage needs a name (max 50 characters)")
        if name in names:
            raise ValueError(f"duplicate stage name: {name}")
        names.add(name)
        if s["kind"] == "shell" and not (isinstance(s.get("run"), str) and s["run"].strip()):
            raise ValueError(f"shell stage {name!r} needs `run`")
        for opt, (lo, hi, integer) in _NUMERIC.items():
            if opt in s:
                v = s[opt]
                if isinstance(v, bool) or not isinstance(v, (int,) if integer else (int, float)) or not lo <= v <= hi:
                    raise ValueError(f"stage {name!r}: {opt} must be {'an integer' if integer else 'a number'} between {lo} and {hi}")
    return stages
