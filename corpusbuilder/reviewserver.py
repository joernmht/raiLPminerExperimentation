"""The review server: the discovery pages on a private network, decisions straight into an inbox.

A deliberately dumb HTTP server for the tailnet (ADR-0021). It does three
things and nothing else:

* ``GET`` serves ``corpus/review/`` read-only (the discovery pages and the
  worklist), plus a web-app manifest so the pages install on a phone;
* ``POST /api/decisions/<paper_key>`` takes one ``discover-decisions`` export,
  validates it with :func:`corpusbuilder.discovergame.validate_export`, and
  appends it as a new file to ``corpus/review/inbox/`` — never into the
  pipeline's ``corpus/decisions/`` (that move is a reviewed step);
* ``GET``/``PUT /api/state/<paper_key>`` keeps the page's working state (the
  same object the page stores in localStorage) so a paper can be continued on
  another device; ``GET /api/progress`` lists what the server holds.

It binds to 127.0.0.1 only; ``tailscale serve`` fronts it for the user's own
devices. No shell, no other routes, bodies capped, paths confined to the
review folder. Run::

    PYTHONPATH=. python3 -m corpusbuilder.reviewserver [--port 8787]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from corpusbuilder.discovergame import INDEX_PAGE, OUT_DIR, REVIEW, validate_export

INBOX = REVIEW / "inbox"
STATE_DIR = REVIEW / "state"
MAX_BODY = 4 * 1024 * 1024
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}$")
_LOCK = threading.Lock()

MANIFEST = {
    "name": "Discovery run",
    "short_name": "Discovery",
    "start_url": "/discover.html",
    "display": "standalone",
    "background_color": "#00103a",
    "theme_color": "#0A777F",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
}
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" '
    'fill="#0A777F"/><text x="50" y="66" font-size="52" text-anchor="middle" font-family="Arial,sans-serif" '
    'font-weight="700" fill="#fff">∀</text></svg>'
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def save_decisions(obj: dict, inbox: Path = INBOX) -> Path:
    """Validate one export and append it to the inbox (never overwrites)."""
    problems = validate_export(obj)
    if problems:
        raise ValueError("; ".join(problems))
    key = obj["paper_key"]
    if not _KEY.match(key):
        raise ValueError("bad paper key")
    inbox.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        path = inbox / f"discover_{key}_{_now()}.json"
        n = 0
        while path.exists():
            n += 1
            path = inbox / f"discover_{key}_{_now()}_{n}.json"
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )
    return path


def save_state(key: str, state: dict, state_dir: Path = STATE_DIR) -> Path:
    if not _KEY.match(key) or not isinstance(state, dict):
        raise ValueError("bad key or state")
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {**state, "saved": _now()}
    path = state_dir / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def load_state(key: str, state_dir: Path = STATE_DIR) -> dict | None:
    if not _KEY.match(key):
        return None
    path = state_dir / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def progress(inbox: Path = INBOX, state_dir: Path = STATE_DIR) -> dict:
    """What the server holds: exports in the inbox and saved states, per paper."""
    out: dict[str, dict] = {}
    if inbox.exists():
        for p in sorted(inbox.glob("discover_*.json")):
            m = re.match(r"discover_(.+)_\d{8}T\d{6}Z(?:_\d+)?\.json$", p.name)
            if m:
                out.setdefault(m.group(1), {"exports": 0, "state": None})["exports"] += 1
    if state_dir.exists():
        for p in sorted(state_dir.glob("*.json")):
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entry = out.setdefault(p.stem, {"exports": 0, "state": None})
            entry["state"] = {
                "saved": st.get("saved"),
                "roles": len(st.get("roles") or {}),
                "indices": len(st.get("indices") or {}),
                "families": len(st.get("families") or {}),
                "marks": len(st.get("marks") or []),
            }
    return out


def _safe_path(root: Path, url_path: str) -> Path | None:
    """The file a URL path names inside ``root``, or None if it escapes or is missing."""
    rel = url_path.split("?", 1)[0].lstrip("/") or "discover.html"
    if rel.endswith("/"):
        rel += "index.html"
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if candidate.name.startswith("_") or "inbox" in candidate.parts or "state" in candidate.parts:
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "railpmining-review/1"
    root: Path = REVIEW
    inbox: Path = INBOX
    state_dir: Path = STATE_DIR

    def log_message(self, fmt: str, *args) -> None:  # quiet, one line per request
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _json(self, status: HTTPStatus, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if length > MAX_BODY
                else HTTPStatus.BAD_REQUEST,
                {"error": "body"},
            )
            return None
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "not JSON"})
            return None
        if not isinstance(obj, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "not an object"})
            return None
        return obj

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/progress":
            self._json(HTTPStatus.OK, {"papers": progress(self.inbox, self.state_dir)})
            return
        m = re.match(r"^/api/state/([^/]+)$", path)
        if m:
            st = load_state(m.group(1), self.state_dir)
            self._json(HTTPStatus.OK if st is not None else HTTPStatus.NOT_FOUND, {"state": st})
            return
        if path == "/manifest.webmanifest":
            body = json.dumps(MANIFEST).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/icon.svg":
            body = ICON_SVG.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        file = _safe_path(self.root, path)
        if file is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else "")
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        m = re.match(r"^/api/decisions/([^/]+)$", self.path.split("?", 1)[0])
        if not m:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        obj = self._read_json()
        if obj is None:
            return
        if obj.get("paper_key") != m.group(1):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "paper key mismatch"})
            return
        try:
            path = save_decisions(obj, self.inbox)
        except ValueError as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        self._json(HTTPStatus.CREATED, {"saved": path.name})

    def do_PUT(self) -> None:
        m = re.match(r"^/api/state/([^/]+)$", self.path.split("?", 1)[0])
        if not m:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        obj = self._read_json()
        if obj is None:
            return
        try:
            save_state(m.group(1), obj, self.state_dir)
        except ValueError as e:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return
        self._json(HTTPStatus.OK, {"ok": True})


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    if not INDEX_PAGE.exists() or not OUT_DIR.exists():
        print(
            f"no pages under {REVIEW}: build them first (python3 -m corpusbuilder.discovergame)",
            file=sys.stderr,
        )
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"review server on http://{host}:{port}/ serving {REVIEW}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
