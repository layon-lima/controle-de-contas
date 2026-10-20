"""Testes offline da API central: nenhuma conexao ao banco real."""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import central_api


class CentralApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), central_api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = Request(self.url + path, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    @patch.dict(os.environ, {"CONTAS_API_TOKEN": "x" * 48})
    def test_private_endpoint_rejects_no_token(self):
        code, data = self.request("/api/v1/status")
        self.assertEqual(code, 401)
        self.assertFalse(data["ok"])

    @patch.dict(os.environ, {"CONTAS_API_TOKEN": "x" * 48})
    def test_private_endpoint_rejects_wrong_token(self):
        code, _ = self.request("/api/v1/status", "y" * 48)
        self.assertEqual(code, 401)

    @patch.dict(os.environ, {"CONTAS_API_TOKEN": "x" * 48})
    @patch("central_api.db_summary", return_value={
        "ok": True, "accounts": 0, "payments": 0, "migration_complete": False
    })
    def test_status_with_valid_token(self, _summary):
        code, data = self.request("/api/v1/status", "x" * 48)
        self.assertEqual(code, 200)
        self.assertFalse(data["migration_complete"])

    @patch.dict(os.environ, {"CONTAS_API_TOKEN": "x" * 48})
    def test_accounts_not_exposed_before_migration(self):
        code, data = self.request("/api/v1/contas", "x" * 48)
        self.assertEqual(code, 503)
        self.assertFalse(data["ok"])

    @patch("central_api.db_health", return_value=True)
    def test_health(self, _health):
        code, data = self.request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(data["database"], "online")

    @patch("central_api.db_health", side_effect=RuntimeError())
    def test_health_fails_closed(self, _health):
        code, data = self.request("/health")
        self.assertEqual(code, 503)
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
