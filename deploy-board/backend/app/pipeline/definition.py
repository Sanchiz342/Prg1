"""Pipeline definition: an ordered list of stages. Each stage has a `kind`:

  checkout      git clone the project's repository at the requested commit (built-in)
  shell         run `run` inside the runner (local process or isolated container)
  docker_build  build an image tagged <project>:<sha7>
  docker_run    (re)start the container from the image
  healthcheck   GET project.health_url until it returns 2xx (retries/interval configurable)
"""
KINDS = {"checkout", "shell", "docker_build", "docker_run", "healthcheck"}

DEFAULT_PIPELINE = [
    {"name": "checkout", "kind": "checkout"},
    {"name": "install", "kind": "shell", "run": "npm ci"},
    {"name": "test", "kind": "shell", "run": "npm test"},
    {"name": "build", "kind": "shell", "run": "npm run build"},
    {"name": "docker", "kind": "docker_build"},
    {"name": "deploy", "kind": "docker_run"},
    {"name": "healthcheck", "kind": "healthcheck", "retries": 5, "interval": 2},
]

ROLLBACK_PIPELINE = [
    {"name": "deploy", "kind": "docker_run"},
    {"name": "healthcheck", "kind": "healthcheck", "retries": 5, "interval": 2},
]


def validate(stages: list[dict]) -> list[dict]:
    names = set()
    for s in stages:
        if s.get("kind") not in KINDS:
            raise ValueError(f"unknown stage kind: {s.get('kind')!r}")
        if not s.get("name"):
            raise ValueError("stage needs a name")
        if s["name"] in names:
            raise ValueError(f"duplicate stage name: {s['name']}")
        names.add(s["name"])
        if s["kind"] == "shell" and not s.get("run"):
            raise ValueError(f"shell stage {s['name']!r} needs `run`")
    return stages
