"""Mock фиксированных GET-примеров из контракта, без записи и бизнес-логики."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from verify_contract import ROOT, load_contract

SPEC = load_contract()
EXAMPLES = SPEC["components"]["examples"]


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Mock-Server", "fixed-examples-only")
        self.send_header("X-Request-ID", str(uuid4()))
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, code, message):
        self.send_json(
            status,
            {
                "code": code,
                "message": message,
                "details": [],
                "request_id": str(uuid4()),
            },
        )

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path == "/openapi.yaml":
            body = (ROOT / "openapi.yaml").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/yaml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        routes = {
            "/v1/books": "BookList",
            "/v1/books/1": "Book",
            "/v1/readers": "ReaderList",
            "/v1/readers/101": "Reader",
            "/v1/loans": "LoanList",
            "/v1/loans/501": "Loan",
        }
        example = routes.get(path)
        if example is None:
            self.error(404, "NOT_FOUND", "Такого GET-примера нет в mock-сервере.")
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query:
            if path == "/v1/loans" and query == {"status": ["overdue"]}:
                example = "OverdueLoanList"
            else:
                self.error(
                    501,
                    "INVALID_REQUEST",
                    "Mock поддерживает только фиксированные GET-примеры и status=overdue; остальные фильтры описаны в контракте.",
                )
                return
        self.send_json(200, EXAMPLES[example]["value"])

    def do_POST(self):
        self.error(
            501,
            "INVALID_REQUEST",
            "Демонстрационный mock не изменяет данные и не исполняет бизнес-правила контракта.",
        )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Library API mock: http://127.0.0.1:{args.port}/v1/books", flush=True)
    print(
        "Fixed GET examples only; stop with Ctrl+C or the PyCharm Stop button.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
