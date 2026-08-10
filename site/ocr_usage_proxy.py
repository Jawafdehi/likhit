# SPDX-License-Identifier: Hippocratic-3.0
"""A counting proxy in front of an OpenAI-compatible OCR endpoint.

`generate.py` reads per-run token usage by taking the difference of a cumulative
counter across each conversion (`LIKHIT_OCR_USAGE_URL` /
`LIKHIT_LOCAL_OCR_USAGE_URL`). Nothing in Likhit or MarkItDown exposes such a
counter -- the token figures live in the OCR response, which the converter does
not surface -- so this serves it: forward every request upstream verbatim,
accumulate the `usage` block each response carries, and publish the totals.

Run one per backend, so each backend's tokens are counted separately and a hosted
run can never be credited with a locally served model's spend:

    python site/ocr_usage_proxy.py --port 8140 --upstream http://127.0.0.1:11434

    export OPENAI_BASE_URL=http://127.0.0.1:8140/v1
    export LIKHIT_LOCAL_OCR_USAGE_URL=http://127.0.0.1:8140/usage

`--upstream` is the server *root*: the request path is forwarded verbatim, so
`/v1/chat/completions` stays `/v1/chat/completions`. `GET /usage` returns
`{"calls", "input_tokens", "output_tokens"}`, the shape `_read_ocr_usage`
requires; every other path is proxied, including `/v1/models`, which the offline
availability check queries.
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Upstreams disagree on spelling: OpenAI-compatible servers report
# prompt_tokens/completion_tokens, while some gateways pass through Anthropic's
# input_tokens/output_tokens. Accept both rather than silently counting zero.
INPUT_KEYS = ("prompt_tokens", "input_tokens")
OUTPUT_KEYS = ("completion_tokens", "output_tokens")
# Bound a proxied body so a runaway upstream cannot exhaust memory here.
MAX_BODY_BYTES = 64 * 1024 * 1024


class Counter:
    """Cumulative totals across every proxied call.

    A conversion runs in a subprocess and may issue several vision calls, so the
    generator only ever reads the difference. Totals therefore never reset.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, usage: dict[str, Any]) -> None:
        inputs = _first_int(usage, INPUT_KEYS)
        outputs = _first_int(usage, OUTPUT_KEYS)
        with self._lock:
            self.calls += 1
            self.input_tokens += inputs
            self.output_tokens += outputs

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            }


def _first_int(usage: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _usage_of(body: bytes) -> dict[str, Any] | None:
    """The `usage` block of a chat-completion response, if it carries one."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else None


def build_handler(upstream: str, counter: Counter) -> type[BaseHTTPRequestHandler]:
    base = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # Signature matches BaseHTTPRequestHandler's, which takes a format string
        # ahead of the arguments; dropping it would break a keyword call.
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Silence per-request logging; the generator's output is the record."""

        def _respond(
            self, status: int, body: bytes, content_type: str, *, close: bool = False
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
            if self.path.rstrip("/") == "/usage":
                body = json.dumps(counter.snapshot()).encode()
                self._respond(200, body, "application/json")
                return
            self._proxy(None)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                # Reading a chunked body would mean de-framing it here. No client
                # in this benchmark sends one, and forwarding an empty body
                # instead would look like a model that answered nothing.
                self._respond(
                    411,
                    b'{"error":"chunked request bodies are not supported"}',
                    "application/json",
                    close=True,
                )
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                # The body is deliberately left unread, so this connection cannot
                # be reused: under HTTP/1.1 keep-alive the leftover bytes would be
                # parsed as the next request line.
                self._respond(
                    413,
                    b'{"error":"request too large"}',
                    "application/json",
                    close=True,
                )
                return
            self._proxy(self.rfile.read(length) if length else b"")

        def _proxy(self, body: bytes | None) -> None:
            request = urllib.request.Request(
                base + self.path,
                data=body,
                method=self.command,
            )
            # Forward content negotiation and auth, but let urllib set Host and
            # the framing headers for the new connection.
            for header in ("Content-Type", "Authorization", "Accept"):
                value = self.headers.get(header)
                if value:
                    request.add_header(header, value)
            try:
                with urllib.request.urlopen(request, timeout=1800) as response:
                    # One byte past the limit distinguishes "exactly at the limit"
                    # from "truncated". Returning a truncated body under the
                    # upstream's 200 would hand the client unparseable JSON and
                    # lose the usage block with it -- a silent miscount, which is
                    # the one failure this proxy must not have.
                    payload = response.read(MAX_BODY_BYTES + 1)
                    if len(payload) > MAX_BODY_BYTES:
                        self._respond(
                            502,
                            b'{"error":"upstream response too large"}',
                            "application/json",
                        )
                        return
                    status = response.status
                    content_type = response.headers.get(
                        "Content-Type", "application/json"
                    )
            except urllib.error.HTTPError as error:
                # Pass the upstream's own error through: likhit's client needs to
                # see the real status to fail the way it would without the proxy.
                # Bounded like the success path -- an error body is no more
                # trustworthy about its size.
                payload = error.read(MAX_BODY_BYTES + 1)
                if len(payload) > MAX_BODY_BYTES:
                    payload = b'{"error":"upstream error response too large"}'
                status, content_type = (
                    error.code,
                    error.headers.get("Content-Type", "application/json"),
                )
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                self._respond(
                    502,
                    json.dumps({"error": f"upstream unreachable: {error}"}).encode(),
                    "application/json",
                )
                return

            usage = _usage_of(payload) if status < 400 else None
            if usage is not None:
                counter.record(usage)
            self._respond(status, payload, content_type)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--upstream",
        required=True,
        help=(
            "server root to forward to, without a path -- "
            "e.g. http://127.0.0.1:11434 for Ollama"
        ),
    )
    args = parser.parse_args(argv)

    # Fail here rather than per request: urllib reads "127.0.0.1:11434" as a URL
    # of scheme "127.0.0.1" and raises URLError, which this proxy reports as a 502
    # "upstream unreachable" on every call. Accurate, but it points at the
    # upstream instead of at the flag that is actually wrong.
    if not args.upstream.startswith(("http://", "https://")):
        parser.error("--upstream must start with http:// or https://")

    counter = Counter()
    # The generator polls /usage while a conversion subprocess holds a request
    # open, so this has to serve concurrently or every read would block.
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), build_handler(args.upstream, counter)
    )
    print(f"counting {args.upstream} on http://127.0.0.1:{args.port} (/usage)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
