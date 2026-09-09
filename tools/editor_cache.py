"""Disposable, content-checked JSON caches for editor preparation work."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(paths):
    return {str(Path(path).resolve()): digest(Path(path).read_bytes()) for path in paths}


def read(path, key):
    try:
        envelope = json.loads(Path(path).read_bytes())
        payload = envelope["payload"]
        if digest(payload.encode("utf-8")) != envelope["sha256"]:
            return None
        record = json.loads(payload)
        if record["key"] != key:
            return None
        for filename, expected in record["files"].items():
            if Path(filename).resolve() != Path(filename):
                return None
            if digest(Path(filename).read_bytes()) != expected:
                return None
        return record["data"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def write(path, key, data, files=()):
    path = Path(path)
    # A cache failure must never turn a successful cook into an authoring error.
    temporary = None
    try:
        payload = json.dumps({"key": key, "data": data, "files": fingerprint(files)}, separators=(",", ":"))
        raw = json.dumps({"payload": payload, "sha256": digest(payload.encode("utf-8"))}).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
