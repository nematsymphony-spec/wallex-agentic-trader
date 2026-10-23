"""Explicit allowlist archive: never includes secrets, private wallets or DBs."""
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def source_files():
    paths = [ROOT / "pyproject.toml", ROOT / "README.md", ROOT / ".gitignore"]
    for directory, extensions in (("src", {".py"}), ("tests", {".py"}),
                                  ("experiments", {".py", ".txt"}), ("config", {".json"}),
                                  ("docs", {".md"}), ("deploy", {".service"})):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in extensions and "__pycache__" not in p.parts)
    return sorted(p for p in paths if p.is_file())


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files()}


def verify_release():
    path = ROOT / "release" / "manifest.json"
    if not path.exists() or json.loads(path.read_text())["files"] != hashes():
        raise ValueError("Release missing or source/config changed; review and freeze the exact submission version")
    return True


def freeze():
    dest = ROOT / "release"
    dest.mkdir(exist_ok=True)
    manifest = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "files": hashes(),
                "note": "Public source snapshot. No keys, account responses or private trading logs. Public URLs and real AI/live verification remain separate deliverables."}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    files = source_files() + [dest / "manifest.json"]
    for folder in ("research-monthly-goals", "credit-validation"):
        files.extend(p for p in (ROOT / "results" / folder).glob("*") if p.suffix in (".json", ".csv", ".md"))
    for name in ("shadow-openai.json", "full-universe-shadow-current.json"):
        path = ROOT / "results" / name
        if path.exists():
            files.append(path)
    reproduction = ROOT / "results/research-monthly-goals/monthly-goals-reproduction.zip"
    if reproduction.exists():
        files.append(reproduction)
    archive = dest / "wallex-submission.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(set(files)):
            z.write(p, str(p.relative_to(ROOT)))
    return {"manifest": str(dest / "manifest.json"), "archive": str(archive), "source_files": len(manifest["files"]),
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
