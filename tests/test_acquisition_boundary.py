import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from spectra_agent import safe_http
from spectra_agent.evidence_search import execute_plans
from spectra_agent.review_samples import record_fact_review
from spectra_agent.sandbox import run_sandboxed


class BoundaryTests(unittest.TestCase):
    def test_dns_blocks_mixed_private_answers(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ["93.184.216.34", "127.0.0.1"]]
        with patch.object(socket, "getaddrinfo", return_value=addresses):
            with self.assertRaises(safe_http.BoundaryError):
                safe_http.resolve_public("https://example.com")

    def test_local_exception_is_exact_origin(self):
        with patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8001))]):
            self.assertTrue(safe_http.resolve_public("http://localhost:8001/api", ["http://localhost:8001"]))
            with self.assertRaises(safe_http.BoundaryError):
                safe_http.resolve_public("http://localhost:8002/api", ["http://localhost:8001"])

    def test_budget_is_shared_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "budget.sqlite"
            first = safe_http.Budget(path, limit=2, interval=0)
            second = safe_http.Budget(path, limit=2, interval=0)
            first.reserve("a", "collection")
            second.reserve("b", "evidence_search")
            with self.assertRaises(safe_http.BudgetExceeded):
                first.reserve("c")
            first.db.close()
            second.db.close()

    def test_real_redirect_does_not_escape_local_exception(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        safe_http.configure_budget(interval=0)
        try:
            with self.assertRaises(safe_http.BoundaryError):
                safe_http.request_bytes(base, allowed_local_origins=[base])
            self.assertEqual(safe_http.current_budget().count(), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_connection_uses_validated_ip_without_second_dns_lookup(self):
        from unittest.mock import MagicMock
        address = ("93.184.216.34", 80)
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.return_value = None
        response.read1.side_effect = [b"body", b""]
        connection.connect.side_effect = lambda: connection._create_connection(("example.com", 80), timeout=5)
        safe_http.configure_budget(interval=0)
        with patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", address)]) as dns, \
             patch.object(socket, "socket") as sock, \
             patch("http.client.HTTPConnection", return_value=connection):
            self.assertEqual(safe_http.request_bytes("http://example.com")[0], b"body")
            self.assertEqual(dns.call_count, 1)
            sock.return_value.connect.assert_called_once_with(address)

    def test_exhausted_credit_stops_batch_and_resume(self):
        from urllib.error import HTTPError
        error = HTTPError("https://api.openai.com", 429, "limited", {}, None)
        error.provider_code = "credit_balance_exhausted"
        search = unittest.mock.Mock(side_effect=error)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "search.json"
            plans = [{"candidate_id": str(i), "status": "pending_search", "queries": [str(i)]} for i in range(4)]
            execute_plans(path, plans, search=search)
            result = execute_plans(path, plans, search=search)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(result["status"], "blocked_provider")

    def test_search_resumes_without_rebilling_and_never_verifies(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "search.json"
            plans = [{"candidate_id": "c", "status": "pending_search", "queries": ["official release", "official metric"]}]
            with patch("spectra_agent.evidence_search.search_openai"):
                search = unittest.mock.Mock(return_value={"urls": ["https://example.com/news"]})
                fetch = lambda url, **kwargs: (b"original source", url)
                first = execute_plans(path, plans, search=search, fetch=fetch)
                second = execute_plans(path, plans, search=search, fetch=fetch)
            self.assertEqual(search.call_count, 2)
            self.assertEqual(len(second["attempts"]), 2)
            self.assertFalse(first["auto_approve"])
            self.assertEqual(second["jobs"][0]["verification_status"], "not_verified")

    def test_search_failure_and_total_limit_are_terminal(self):
        with tempfile.TemporaryDirectory() as folder:
            search = unittest.mock.Mock(side_effect=TimeoutError())
            plans = [{"candidate_id": str(i), "status": "pending_search", "queries": [str(i)]} for i in range(9)]
            path = Path(folder) / "search.json"
            execute_plans(path, plans, search=search)
            result = execute_plans(path, plans, search=search)
            self.assertEqual(search.call_count, 4)
            self.assertEqual(result["jobs"][-1]["status"], "budget_exhausted_needs_review")

    def test_samples_keep_changes_and_are_not_training_data(self):
        with tempfile.TemporaryDirectory() as folder:
            before = {"records": [{"candidate_id": "c", "title": "title", "suggested_evidence": [{"claim": "old"}]}]}
            after = {"verified_by": "reviewer", "records": [{"candidate_id": "c", "decision": "include", "suggested_evidence": [{"claim": "old", "human_fact_text": "corrected"}]}]}
            for _ in range(2):
                record_fact_review(Path(folder), before, after, "human")
            with sqlite3.connect(Path(folder) / "review-samples.sqlite") as db:
                rows = db.execute("SELECT payload FROM samples").fetchall()
            self.assertEqual(len(rows), 1)
            sample = json.loads(rows[0][0])
            self.assertFalse(sample["training_eligible"])
            self.assertEqual(sample["decision"]["suggested_evidence"][0]["human_fact_text"], "corrected")

    def test_real_sandbox_denies_network_files_process_and_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / "secret-fixture"
            fixture.write_text("TEST_ONLY")
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            try:
                output = run_sandboxed([sys.executable, str(Path(__file__).parent / "fixtures/sandbox_probe.py")],
                    input_text=json.dumps({"path": str(fixture), "port": server.getsockname()[1]}))
                self.assertEqual(json.loads(output.stdout), {"read": "denied", "write": "denied", "network": "denied", "process": "denied", "secret_in_environment": False})
            finally:
                server.close()
