"""Message composer payload integrity: canonical content, size limits,
target-binding integrity, atomic idempotency, and HTTP framing safety
(part of commit 4).
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "apps", "control-plane"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import server  # noqa: E402
import clearwright_message as cwm  # noqa: E402


class CanonicalContentTests(unittest.TestCase):

    def test_crlf_and_cr_normalize_to_lf(self):
        self.assertEqual(cwm.canonical_content("a\r\nb\rc"), "a\nb\nc")

    def test_generated_at_and_whitespace_are_stripped(self):
        self.assertEqual(cwm.canonical_content("  hello  \n"), "hello")

    def test_canonical_sha256_matches_utf8_bytes(self):
        import hashlib
        text = "hi\r\nthere"
        expected = hashlib.sha256(cwm.canonical_content(text).encode("utf-8")).hexdigest()
        self.assertEqual(cwm.canonical_sha256(text), expected)


class BuildMessageSizeLimitTests(unittest.TestCase):

    def test_exact_limit_accepted(self):
        content = "x" * cwm.MESSAGE_MAX_BYTES
        msg = cwm.build_message("OPERATOR-0001", content, direction="inbound")
        self.assertEqual(len(msg["message"].encode("utf-8")), cwm.MESSAGE_MAX_BYTES)

    def test_limit_plus_one_rejected(self):
        content = "x" * (cwm.MESSAGE_MAX_BYTES + 1)
        with self.assertRaises(cwm.MessageTooLarge):
            cwm.build_message("OPERATOR-0001", content, direction="inbound")

    def test_message_too_large_is_a_value_error(self):
        content = "x" * (cwm.MESSAGE_MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            cwm.build_message("OPERATOR-0001", content, direction="inbound")

    def test_unicode_content_measured_in_utf8_bytes(self):
        # A multi-byte UTF-8 character (combining + astral-plane) must count
        # its actual encoded bytes, not code points.
        char = "\U0001F600"  # 4 UTF-8 bytes
        content = char * (cwm.MESSAGE_MAX_BYTES // 4)
        msg = cwm.build_message("OPERATOR-0001", content, direction="inbound")
        self.assertEqual(len(msg["message"].encode("utf-8")), cwm.MESSAGE_MAX_BYTES)
        with self.assertRaises(cwm.MessageTooLarge):
            cwm.build_message("OPERATOR-0001", content + char, direction="inbound")


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="idem_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(os.path.join(base, "active"))

    def test_exact_retry_returns_existing_message(self):
        msg1 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-idem",
                                 idempotency_key="key-1")
        stored1, retry1 = cwm.write_message_idempotent(self.root, msg1)
        self.assertFalse(retry1)
        msg2 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-idem",
                                 idempotency_key="key-1")
        stored2, retry2 = cwm.write_message_idempotent(self.root, msg2)
        self.assertTrue(retry2)
        self.assertEqual(stored1["message_id"], stored2["message_id"])
        self.assertEqual(len(cwm.read_messages(self.root, thread_id="thr-idem")), 1)

    def test_same_key_different_content_conflicts(self):
        msg1 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-idem2",
                                 idempotency_key="key-2")
        cwm.write_message_idempotent(self.root, msg1)
        msg2 = cwm.build_message("OPERATOR-0001", "different text", thread_id="thr-idem2",
                                 idempotency_key="key-2")
        with self.assertRaises(cwm.IdempotencyConflict) as ctx:
            cwm.write_message_idempotent(self.root, msg2)
        self.assertEqual(ctx.exception.existing["message"], "hello")

    def test_same_key_different_target_conflicts(self):
        msg1 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-idem3",
                                 idempotency_key="key-3", work_item_id="message:wi-a")
        cwm.write_message_idempotent(self.root, msg1)
        msg2 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-idem3",
                                 idempotency_key="key-3", work_item_id="message:wi-b")
        with self.assertRaises(cwm.IdempotencyConflict):
            cwm.write_message_idempotent(self.root, msg2)

    def test_no_key_always_writes_a_new_message(self):
        msg1 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-no-idem")
        cwm.write_message_idempotent(self.root, msg1)
        msg2 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-no-idem")
        cwm.write_message_idempotent(self.root, msg2)
        self.assertEqual(len(cwm.read_messages(self.root, thread_id="thr-no-idem")), 2)

    def test_binding_scoped_lookups_require_matching_thread(self):
        msg1 = cwm.build_message("OPERATOR-0001", "hello", thread_id="thr-scope-a",
                                 idempotency_key="key-scope")
        stored, _ = cwm.write_message_idempotent(self.root, msg1)
        # Correct thread resolves.
        found = cwm.find_by_message_id(self.root, "thr-scope-a", stored["message_id"])
        self.assertIsNotNone(found)
        # Wrong thread does NOT resolve, even with the right message_id --
        # this is what keeps the lookup from being an enumeration surface.
        found_wrong = cwm.find_by_message_id(self.root, "thr-scope-b", stored["message_id"])
        self.assertIsNone(found_wrong)
        found_key_wrong = cwm.find_by_idempotency_key(self.root, "thr-scope-b", "key-scope")
        self.assertIsNone(found_key_wrong)


class DoMessageTests(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="domsg_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(os.path.join(base, "active"))

    def test_normal_post_succeeds_with_identity_and_hash(self):
        result = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "message": "hello", "role": "operator",
        })
        self.assertTrue(result["ok"])
        self.assertIn("message_id", result)
        self.assertIn("canonical_sha256", result)
        self.assertEqual(server._message_status_code(result), 200)

    def test_oversized_content_returns_message_too_large(self):
        result = server.do_message(self.root, {
            "actor": "OPERATOR-0001",
            "message": "x" * (cwm.MESSAGE_MAX_BYTES + 1),
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "message_too_large")
        self.assertEqual(server._message_status_code(result), 413)

    def test_target_mismatch_refused(self):
        first = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "message": "start",
            "work_item_id": "message:wi-x",
        })
        self.assertTrue(first["ok"])
        thread_id = first["thread_id"]
        result = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "message": "second",
            "thread_id": thread_id, "work_item_id": "message:wi-DIFFERENT",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "target_mismatch")
        self.assertEqual(server._message_status_code(result), 400)

    def test_idempotent_retry_via_do_message(self):
        body = {"actor": "OPERATOR-0001", "message": "hi",
                "thread_id": "thr-http-idem", "idempotency_key": "key-http-1"}
        r1 = server.do_message(self.root, body)
        r2 = server.do_message(self.root, body)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r1["message_id"], r2["message_id"])
        self.assertTrue(r2["idempotent_retry"])

    def test_idempotency_conflict_via_do_message(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "message": "hi",
                                      "thread_id": "thr-http-idem2",
                                      "idempotency_key": "key-http-2"})
        result = server.do_message(self.root, {"actor": "OPERATOR-0001",
                                                "message": "different",
                                                "thread_id": "thr-http-idem2",
                                                "idempotency_key": "key-http-2"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "idempotency_conflict")
        self.assertEqual(server._message_status_code(result), 409)


class HttpFramingTests(unittest.TestCase):
    """Real HTTP-level framing behavior: Content-Length validation happens
    BEFORE any body is read, and oversized/malformed requests never silently
    truncate."""

    @classmethod
    def setUpClass(cls):
        base = tempfile.mkdtemp(prefix="http_frame_")
        cls._base = base
        server.QUEUE_ROOT, *_ = server.resolve_queue(os.path.join(base, "active"))
        server.DURABLE = True
        server.MODE = server.OPERATOR_MODE
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls._base, ignore_errors=True)

    def _post(self, path, body_bytes, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", path, body=body_bytes, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def test_normal_post_returns_200(self):
        body = json.dumps({"actor": "OPERATOR-0001", "message": "hello via http"}).encode("utf-8")
        status, data = self._post("/api/messages", body,
                                  {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertTrue(result["ok"])

    def test_oversized_request_body_returns_413_without_reading(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(server.REQUEST_MAX_BYTES + 1))
            conn.endheaders()
            # Deliberately do NOT send the declared body -- if the server read
            # before checking the length, this request would hang/time out.
            resp = conn.getresponse()
            self.assertEqual(resp.status, 413)
        finally:
            conn.close()

    def test_oversized_message_content_returns_413(self):
        body = json.dumps({"actor": "OPERATOR-0001",
                           "message": "x" * (cwm.MESSAGE_MAX_BYTES + 1)}).encode("utf-8")
        status, data = self._post("/api/messages", body,
                                  {"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        result = json.loads(data)
        self.assertEqual(result["error_code"], "message_too_large")

    def test_non_numeric_content_length_returns_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "not-a-number")
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
        finally:
            conn.close()

    def test_chunked_without_content_length_returns_411(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Transfer-Encoding", "chunked")
            conn.endheaders()
            resp = conn.getresponse()
            self.assertEqual(resp.status, 411)
        finally:
            conn.close()

    def test_binding_scoped_message_lookup_via_http(self):
        body = json.dumps({"actor": "OPERATOR-0001", "message": "lookup me",
                           "thread_id": "thr-http-lookup"}).encode("utf-8")
        status, data = self._post("/api/messages", body,
                                  {"Content-Type": "application/json"})
        posted = json.loads(data)
        mid = posted["message_id"]

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/api/messages?thread_id=thr-http-lookup&message_id=" + mid)
            resp = conn.getresponse()
            found = json.loads(resp.read())
            self.assertTrue(found["found"])
            self.assertEqual(found["message"]["message"], "lookup me")

            # A different (wrong) thread_id must not resolve the same id.
            conn.request("GET", "/api/messages?thread_id=thr-wrong&message_id=" + mid)
            resp2 = conn.getresponse()
            not_found = json.loads(resp2.read())
            self.assertFalse(not_found["found"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
