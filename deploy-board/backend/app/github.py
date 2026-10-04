import hashlib
import hmac
import re


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def normalize_repo(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^(https?://|ssh://)?(git@)?", "", u)
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/0-9]", u) else u
    u = re.sub(r"\.git$", "", u).rstrip("/")
    return u


def repo_matches(project_url: str, payload_repo: dict) -> bool:
    mine = normalize_repo(project_url)
    for key in ("clone_url", "html_url", "ssh_url", "git_url", "url"):
        if payload_repo.get(key) and normalize_repo(payload_repo[key]) == mine:
            return True
    return False


def parse_push(payload: dict) -> dict | None:
    """Extract {branch, commit, author} from a GitHub `push` payload (None if not a branch push)."""
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/") or payload.get("deleted"):
        return None
    head = payload.get("head_commit") or {}
    return {
        "branch": ref.removeprefix("refs/heads/"),
        "commit": payload.get("after") or head.get("id", ""),
        "author": (head.get("author") or {}).get("name") or (payload.get("pusher") or {}).get("name", ""),
    }
