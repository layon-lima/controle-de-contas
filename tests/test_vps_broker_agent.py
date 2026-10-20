"""Testes isolados: comunicacao com o PC SEM transferir qualquer PDF."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deploy.vps.broker import Queue, make_handler, validate_secrets
from tools.CONTAS_AGENTE_VPS import Remote, pdf_status, run_one, validate_broker_url

ADMIN_TOKEN = "A" * 48
AGENT_TOKEN = "B" * 48


class WindowsDocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contas_agent_test_")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "BOLETOS"
        self.pdf = self.root / "BOLETOS 2027" / "01 - JANEIRO" / "conta.pdf"
        self.pdf.parent.mkdir(parents=True)
        self.original_pdf = b"%PDF-1.4 original mantido no PC"
        self.pdf.write_bytes(self.original_pdf)
        self.db = self.home / "controle_contas.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE contas(id INTEGER PRIMARY KEY, pdf_conta TEXT)")
            con.execute("INSERT INTO contas VALUES(1,?)", (str(self.pdf),))
            con.execute("INSERT INTO contas VALUES(2,?)", (str(self.home / 'BOLETOS_OLD' / 'outro.pdf'),))
            con.execute("INSERT INTO contas VALUES(3,?)", (str(self.root / 'missing.pdf'),))

    def test_only_local_pdf_metadata_no_path_or_bytes(self):
        before_db = self.db.read_bytes()
        value = pdf_status(self.db, self.root, 1)
        self.assertEqual(value, {
            "available": True,
            "within_official_root": True,
            "size_bytes": len(self.original_pdf),
        })
        self.assertNotIn(str(self.pdf), json.dumps(value))
        self.assertNotIn("original mantido", json.dumps(value))
        self.assertEqual(self.pdf.read_bytes(), self.original_pdf)
        self.assertEqual(before_db, self.db.read_bytes())

    def test_does_not_read_or_expose_outside_official_folder(self):
        other = self.home / "outside.pdf"
        other.write_bytes(b"NAO TRANSFERIR")
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE contas SET pdf_conta=? WHERE id=2", (str(other),))
        self.assertFalse(pdf_status(self.db, self.root, 2)["available"])
        self.assertFalse(pdf_status(self.db, self.root, 3)["available"])
        self.assertFalse(pdf_status(self.db, self.root, 999)["available"])

    def test_no_database_created_when_offline(self):
        missing = self.home / "absent.db"
        self.assertFalse(pdf_status(missing, self.root, 1)["available"])
        self.assertFalse(missing.exists())

    def test_agent_requires_https_except_localhost_testing(self):
        with self.assertRaises(ValueError):
            validate_broker_url("http://contas-cloud.fazendanovohorizonte.com")
        with self.assertRaises(ValueError):
            validate_broker_url("https://example.com/?secret=hidden")
        validate_broker_url("https://contas-cloud.fazendanovohorizonte.com/__agente")
        validate_broker_url("http://127.0.0.1:18000")


class BrokerQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contas_broker_test_")
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(Path(self.temp.name) / "jobs.sqlite")

    def test_job_idempotency_and_lease(self):
        first = self.queue.enqueue("request-123456789abcdef", 13)
        second = self.queue.enqueue("request-123456789abcdef", 13)
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(ValueError):
            self.queue.enqueue("request-123456789abcdef", 14)
        job = self.queue.claim()
        self.assertEqual(job["account_id"], 13)
        self.assertEqual(job["operation"], "pdf_status")
        self.assertIsNone(self.queue.claim())
        self.assertFalse(self.queue.complete(job["id"], "wrong-lease-key-not-right", {
            "available": True, "within_official_root": True, "size_bytes": 5
        }))
        self.assertTrue(self.queue.complete(job["id"], job["lease_token"], {
            "available": True, "within_official_root": True, "size_bytes": 5
        }))
        self.assertEqual(self.queue.inspect(job["id"])["status"], "done")
        self.assertIsNone(self.queue.claim())
        self.assertFalse(self.queue.complete(job["id"], job["lease_token"], {
            "available": True, "within_official_root": True, "size_bytes": 5
        }))

    def test_offline_queue_waits_and_does_not_process_pdf(self):
        item = self.queue.enqueue("offline-req-1234567890", 1)
        self.assertEqual(self.queue.inspect(item["id"])["status"], "queued")
        self.assertIsNone(self.queue.inspect(item["id"])["result"])

    def test_tokens_and_bad_output_denied(self):
        with self.assertRaises(ValueError):
            validate_secrets("bad", AGENT_TOKEN)
        with self.assertRaises(ValueError):
            validate_secrets(AGENT_TOKEN, AGENT_TOKEN)
        job = self.queue.claim()
        self.assertIsNone(job)
        created = self.queue.enqueue("safeguard-1234567890", 1)
        leased = self.queue.claim()
        with self.assertRaises(ValueError):
            self.queue.complete(leased["id"], leased["lease_token"], {
                "available": True, "within_official_root": True,
                "size_bytes": 2, "contents": "PDF SECRET"
            })
        self.assertEqual(self.queue.inspect(created["id"])["status"], "leased")


class BrokerHttpIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.queue = Queue(self.folder / "queue.sqlite")
        handler = make_handler(self.queue, ADMIN_TOKEN, AGENT_TOKEN)
        self.web = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.web.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = "http://127.0.0.1:" + str(self.web.server_port)
        self.docs = self.folder / "BOLETOS"
        self.pdf = self.docs / "BOLETOS 2027" / "01 - JANEIRO" / "boleto.pdf"
        self.pdf.parent.mkdir(parents=True)
        self.pdf.write_bytes(b"FILE MUST STAY ON WINDOWS")
        self.database = self.folder / "contas.db"
        with sqlite3.connect(self.database) as con:
            con.execute("CREATE TABLE contas(id INTEGER PRIMARY KEY, pdf_conta TEXT)")
            con.execute("INSERT INTO contas VALUES(7,?)", (str(self.pdf),))

    def stop_server(self):
        self.web.shutdown()
        self.web.server_close()
        self.thread.join(timeout=2)

    def request(self, token, method, path, data=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=body, headers=headers, method=method)
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)

    def test_integration_with_no_pdf_transit(self):
        item = {"request_key": "vps-integration-123456", "account_id": 7,
                "operation": "pdf_status"}
        with self.assertRaises(HTTPError) as unauthorized:
            self.request("X" * 48, "POST", "/internal/jobs", item)
        self.assertEqual(unauthorized.exception.code, 401)
        code, created = self.request(ADMIN_TOKEN, "POST", "/internal/jobs", item)
        self.assertEqual(code, 202)
        remote = Remote(self.base, AGENT_TOKEN)
        self.assertTrue(run_one(remote, self.database, self.docs))
        self.assertFalse(run_one(remote, self.database, self.docs))
        code, result = self.request(
            ADMIN_TOKEN, "GET", "/internal/jobs/" + created["job"]["id"]
        )
        self.assertEqual(result["job"]["status"], "done")
        self.assertEqual(result["job"]["result"]["size_bytes"], self.pdf.stat().st_size)
        self.assertNotIn("FILE MUST", json.dumps(result))
        self.assertNotIn(str(self.pdf), json.dumps(result))
        self.assertEqual(self.pdf.read_bytes(), b"FILE MUST STAY ON WINDOWS")
        with self.assertRaises(HTTPError) as blocked:
            self.request(AGENT_TOKEN, "GET", "/internal/jobs/" + created["job"]["id"])
        self.assertEqual(blocked.exception.code, 401)

    def test_unsupported_command_does_not_enqueue_anything(self):
        with self.assertRaises(HTTPError) as blocked:
            self.request(ADMIN_TOKEN, "POST", "/internal/jobs", {
                "request_key": "unsupported-1234567890", "account_id": 7,
                "operation": "merge_receipt"
            })
        self.assertEqual(blocked.exception.code, 400)
        self.assertIsNone(self.queue.claim())


if __name__ == "__main__":
    unittest.main()
