"""corpusbuilder.reviewserver — read-only pages, validated inbox, confined paths, state sync."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from corpusbuilder import reviewserver


@pytest.fixture()
def server(tmp_path: Path):
    root = tmp_path / "review"
    (root / "discover").mkdir(parents=True)
    (root / "discover.html").write_text("<html>index</html>", encoding="utf-8", newline="\n")
    (root / "discover" / "p.html").write_text("<html>page</html>", encoding="utf-8", newline="\n")
    (root / "_secret.json").write_text("{}", encoding="utf-8", newline="\n")

    class H(reviewserver.Handler):
        pass

    H.root, H.inbox, H.state_dir = root, root / "inbox", root / "state"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1], root
    httpd.shutdown()


def _req(port: int, method: str, path: str, body: dict | None = None):
    c = HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except json.JSONDecodeError:
        return r.status, raw.decode("utf-8", "replace")


def test_pages_are_served_and_paths_are_confined(server) -> None:
    port, root = server
    assert _req(port, "GET", "/") == (200, "<html>index</html>")
    assert _req(port, "GET", "/discover/p.html") == (200, "<html>page</html>")
    assert _req(port, "GET", "/../../etc/passwd")[0] == 404
    assert _req(port, "GET", "/_secret.json")[0] == 404
    assert _req(port, "GET", "/manifest.webmanifest")[1]["display"] == "standalone"
    assert _req(port, "GET", "/icon.svg")[0] == 200


def test_decisions_go_to_the_inbox_only_when_valid(server) -> None:
    port, root = server
    good = {
        "schema_version": "discover-decisions-2",
        "paper_key": "p",
        "roles": {
            "m-0001": {
                "role": "definition",
                "span": {"text": "Let x be.", "para": 1, "start": 0, "end": 9},
                "source": "human",
            }
        },
        "marks": [],
        "indices": {"i": {"verdict": "label", "family": ""}},
        "families": {},
        "label_proposals": ["st"],
    }
    status, body = _req(port, "POST", "/api/decisions/p", good)
    assert status == 201 and body["saved"].startswith("discover_p_")
    files = list((root / "inbox").glob("*.json"))
    assert (
        len(files) == 1
        and json.loads(files[0].read_text(encoding="utf-8"))["indices"]["i"]["verdict"] == "label"
    )
    assert (
        _req(port, "POST", "/api/decisions/p", good)[0] == 201
        and len(list((root / "inbox").glob("*.json"))) == 2
    )
    assert _req(port, "POST", "/api/decisions/q", good) == (400, {"error": "paper key mismatch"})
    bad = dict(good, roles={"m-0001": {"role": "maybe"}})
    assert _req(port, "POST", "/api/decisions/p", bad)[0] == 400
    assert (
        _req(
            port,
            "POST",
            "/api/decisions/p",
            {"schema_version": "vocab-decisions-1", "paper_key": "p"},
        )[0]
        == 400
    )
    assert _req(port, "POST", "/api/other", good)[0] == 404
    assert not (root / "decisions").exists()  # never the pipeline's folder


def test_state_round_trip_and_progress(server) -> None:
    port, root = server
    assert _req(port, "GET", "/api/state/p") == (404, {"state": None})
    st = {
        "roles": {"m-0001": {"role": "formula"}},
        "marks": [],
        "indices": {"i": {"verdict": "index", "family": "I"}},
        "families": {},
        "labels": {},
        "sel": None,
        "round": "defs",
        "pos": {},
    }
    assert _req(port, "PUT", "/api/state/p", st)[0] == 200
    status, body = _req(port, "GET", "/api/state/p")
    assert status == 200 and body["state"]["round"] == "defs" and body["state"]["saved"]
    assert _req(port, "PUT", "/api/state/../x", st)[0] in (400, 404)
    assert _req(port, "GET", "/api/progress")[1]["papers"]["p"]["state"]["indices"] == 1
    assert _req(port, "GET", "/api/state/p.json")[0] == 404  # state files are not served as pages
