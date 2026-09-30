import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from cicdanalysis.config import Config
from cicdanalysis.db import Database
from cicdanalysis.openapi import openapi_spec
from cicdanalysis.reports import Reports
from cicdanalysis.web import handler_factory, read_token_ok

TOKEN = "read-token-123"
READ_PATHS = ["/", "/docs", "/redoc", "/openapi.json", "/api/v1/overview", "/api/v1/builds",
              "/api/v1/users", "/api/v1/failures", "/api/v1/triggers"]


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class WebAuthTests(unittest.TestCase):
    def serve(self, **config):
        cfg = Config(**{"read_api_token": TOKEN, "webhook_secret": "hook", "trigger_webhook_secret": "hook", **config})
        app = SimpleNamespace(config=cfg, db=self.db, reports=Reports(self.db, "Asia/Kuala_Lumpur"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def get(self, path, auth=None):
        req = urllib.request.Request(self.base + path, headers={"Authorization": auth} if auth else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        self.db.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_endpoints_reject_missing_or_wrong_token(self):
        self.serve()
        for path in READ_PATHS:
            for auth in (None, "Bearer wrong", basic("x", "wrong"), "Token " + TOKEN):
                with self.subTest(path=path, auth=auth):
                    status, headers, body = self.get(path, auth)
                    self.assertEqual(status, 401)
                    self.assertEqual(json.loads(body), {"error": "unauthorized"})
                    self.assertIn("Basic", headers["WWW-Authenticate"])

    def test_read_endpoints_accept_bearer_and_basic(self):
        self.serve()
        for path in READ_PATHS:
            for auth in ("Bearer " + TOKEN, basic("anyone", TOKEN), basic("", TOKEN)):
                with self.subTest(path=path, auth=auth):
                    self.assertEqual(self.get(path, auth)[0], 200)

    def test_unknown_path_needs_token_before_404(self):
        self.serve()
        self.assertEqual(self.get("/api/v1/nope")[0], 401)
        self.assertEqual(self.get("/api/v1/nope", "Bearer " + TOKEN)[0], 404)

    def test_unset_token_fails_closed(self):
        self.serve(read_api_token="")
        self.assertEqual(self.get("/api/v1/builds")[0], 401)
        self.assertEqual(self.get("/api/v1/builds", "Bearer ")[0], 401)
        self.assertEqual(self.get("/api/v1/builds", basic("x", ""))[0], 401)

    def test_health_and_metrics_public_by_default(self):
        self.serve()
        for path in ("/healthz", "/readyz", "/metrics"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 200)

    def test_health_and_metrics_can_require_token(self):
        self.serve(public_health=False, public_metrics=False)
        for path in ("/healthz", "/readyz", "/metrics"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 401)
                self.assertEqual(self.get(path, "Bearer " + TOKEN)[0], 200)

    def test_metrics_private_while_health_stays_public(self):
        self.serve(public_metrics=False)
        self.assertEqual(self.get("/healthz")[0], 200)
        self.assertEqual(self.get("/metrics")[0], 401)

    def test_read_token_does_not_open_webhooks(self):
        self.serve()
        req = urllib.request.Request(self.base + "/api/v1/webhooks/jenkins", data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_read_token_ok_rejects_malformed_basic(self):
        self.assertFalse(read_token_ok("Basic !!!notbase64", TOKEN))
        self.assertFalse(read_token_ok("Basic " + base64.b64encode(b"\xff\xfe").decode(), TOKEN))
        self.assertFalse(read_token_ok("Bearer " + TOKEN, ""))
        self.assertTrue(read_token_ok("bearer " + TOKEN, TOKEN))

    def test_openapi_documents_read_auth(self):
        spec = openapi_spec()
        self.assertIn("ReadBearer", spec["components"]["securitySchemes"])
        for path in ("/api/v1/overview", "/api/v1/builds", "/api/v1/users", "/api/v1/failures", "/api/v1/triggers"):
            op = spec["paths"][path]["get"]
            self.assertEqual(op["security"], [{"ReadBearer": []}, {"ReadBasic": []}])
            self.assertIn("401", op["responses"])
        self.assertNotIn("security", spec["paths"]["/healthz"]["get"])


if __name__ == "__main__":
    unittest.main()
