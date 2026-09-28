"""Local dashboard server. It reads the processed database and does not hardcode course lists."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bitsrec.dashboard.compare import compare_courses
from bitsrec.dashboard.feed import build_feed, build_sections
from bitsrec.dashboard.freshness import dataset_freshness
from bitsrec.dashboard.options import profile_options
from bitsrec.dashboard.transcript import parse_transcript
from bitsrec.engine.eligibility import explain_course
from bitsrec.engine.progress import plan_terms, suggest_completed
from bitsrec.llm.query import parse_query
from bitsrec.paths import processed_dir, project_root
from bitsrec.schedule.combine import combine_sections

_STATIC = Path(__file__).resolve().parent / "static"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Serve the BITS course dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(processed_dir() / "bits.sqlite"))
    args = parser.parse_args()
    server = make_server(args.host, args.port, args.db)
    print(f"Dashboard at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_server(host: str, port: int, db_path: str | Path) -> ThreadingHTTPServer:
    handler = _handler(Path(db_path))
    server = ThreadingHTTPServer((host, port), handler)
    server.db_path = str(db_path)
    return server


def _handler(db_path: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        database = db_path

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/":
                self._bytes(200, "text/html; charset=utf-8", (_STATIC / "index.html").read_bytes())
                return
            if self.path.split("?", 1)[0] == "/api/example":
                example = project_root() / "data" / "examples" / "student_profile.example.json"
                self._bytes(200, "application/json", example.read_bytes())
                return
            if self.path.split("?", 1)[0] == "/api/options":
                self._json(200, profile_options(self.database))
                return
            if self.path.split("?", 1)[0] == "/api/freshness":
                self._json(200, dataset_freshness(self.database))
                return
            self._json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                payload = self._payload()
            except json.JSONDecodeError:
                self._json(400, {"error": "Request body must be JSON."})
                return
            try:
                if path == "/api/feed":
                    result = build_feed(
                        payload.get("profile") or {},
                        str(payload.get("query") or ""),
                        self.database,
                        limit=payload.get("limit", 20),
                        offset=payload.get("offset", 0),
                        unverified_offset=payload.get("unverified_offset", 0),
                        held_sections=payload.get("held_sections") or [],
                    )
                elif path == "/api/sections":
                    result = build_sections(
                        str(payload.get("course_code") or ""),
                        payload.get("held_sections") or [],
                        self.database,
                    )
                elif path == "/api/suggest-completed":
                    result = suggest_completed(payload.get("profile") or {}, self.database)
                elif path == "/api/transcript":
                    result = parse_transcript(str(payload.get("text") or ""))
                elif path == "/api/explain":
                    result = explain_course(
                        payload.get("profile") or {},
                        str(payload.get("course_code") or ""),
                        self.database,
                    )
                elif path == "/api/plan":
                    result = plan_terms(payload.get("profile") or {}, self.database)
                elif path == "/api/combine":
                    profile = payload.get("profile") or {}
                    result = combine_sections(
                        payload.get("course_codes") or [],
                        payload.get("held_sections") or [],
                        self.database,
                        parse_query(str(payload.get("query") or ""))["schedule"],
                    )
                    result["campus"] = profile.get("campus")
                elif path == "/api/compare":
                    profile = payload.get("profile") or {}
                    result = compare_courses(
                        payload.get("course_codes") or [],
                        str(profile.get("campus") or ""),
                        self.database,
                    )
                else:
                    self._json(404, {"error": "Not found."})
                    return
            except (ValueError, FileNotFoundError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, result)

        def _payload(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise json.JSONDecodeError("body too large", "", 0)
            raw = self.rfile.read(length) if length else b"{}"
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise json.JSONDecodeError("object required", "", 0)
            return parsed

        def _json(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self._bytes(status, "application/json", encoded)

        def _bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return DashboardHandler
