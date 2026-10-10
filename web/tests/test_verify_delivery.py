"""Small HTTP fixtures prove delivery without uploading or downloading the book."""

import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_delivery.py"
spec = importlib.util.spec_from_file_location("verify_delivery", SCRIPT)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.payload = bytes(range(256)) * 40
        self.mode = "good"
        self.requests = []
        self.cache_headers = None
        self.cors_origins = ["https://reader.example.com"]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.respond(True)

            def do_GET(self):
                self.respond(False)

            def respond(self, head):
                owner.requests.append((self.command, self.path, dict(self.headers)))
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", owner.origin + "/private?credential=hidden")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if owner.mode == "wrong-416" and self.headers.get("Range") == f"bytes={len(owner.payload)}-{len(owner.payload)}":
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(owner.payload) + 1}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = owner.payload
                status, body, content_range = 200, payload, None
                if not head and self.headers.get("Range"):
                    bounds = self.headers["Range"].removeprefix("bytes=").split("-")
                    start, end = int(bounds[0]), int(bounds[1])
                    if start >= len(payload):
                        status, body = 416, b""
                        content_range = f"bytes */{len(payload)}"
                    else:
                        end = min(end, len(payload) - 1)
                        status, body = 206, payload[start:end + 1]
                        content_range = f"bytes {start}-{end}/{len(payload)}"
                        if owner.mode == "ignores-range":
                            status, body, content_range = 200, payload, None
                        elif owner.mode == "wrong-total":
                            content_range = f"bytes {start}-{end}/{len(payload) + 1}"
                        elif owner.mode == "wrong-start":
                            content_range = f"bytes {start + 1}-{end}/{len(payload)}"
                        elif owner.mode == "short-body":
                            body = body[:-1]
                        elif owner.mode == "wrong-bytes":
                            body = bytes([body[0] ^ 1]) + body[1:]
                        elif owner.mode == "oversized-body":
                            body += b"x" * 8192
                elif not head and owner.mode == "wrong-full-bytes":
                    body = bytes([payload[0] ^ 1]) + payload[1:]
                self.send_response(status)
                if owner.mode != "missing-length" or not head:
                    self.send_header("Content-Length", str(len(payload) if head else len(body)))
                self.send_header("Content-Type", "text/html" if owner.mode == "wrong-type" else "audio/mpeg")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"a-provider-specific-etag"')
                policies = owner.cache_headers if owner.cache_headers is not None else [
                    "no-store" if owner.mode == "no-cache" else "public, max-age=31536000, immutable"]
                for policy in policies:
                    self.send_header("Cache-Control", policy)
                if owner.mode != "missing-cors":
                    for origin in owner.cors_origins:
                        self.send_header("Access-Control-Allow-Origin", origin)
                    if owner.mode != "unexposed-range":
                        self.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, ETag")
                if content_range:
                    self.send_header("Content-Range", content_range)
                if owner.mode == "encoded-range" and status == 206:
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                if not head:
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.inventory = {
            "schemaVersion": 1,
            "appOrigin": "https://reader.example.com",
            "mediaOrigins": [self.origin],
            "assets": [{
                "url": self.origin + "/audio.mp3",
                "sha256": hashlib.sha256(self.payload).hexdigest(),
                "bytes": len(self.payload),
                "contentType": "audio/mpeg",
                "immutable": True,
                "samples": [{"start": start, "end": end,
                             "sha256": hashlib.sha256(self.payload[start:end + 1]).hexdigest()}
                            for start, end in delivery.sample_ranges(len(self.payload))],
            }],
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def verify(self, **kwargs):
        return delivery.verify_inventory(self.inventory, allow_origins=[self.origin],
                                         allow_loopback=True, **kwargs)

    def test_head_and_three_exact_ranges_and_416(self):
        result = self.verify()
        self.assertTrue(result["deliveryVerified"], result)
        self.assertEqual(result["downloadedBytes"], 3 * 1024)
        self.assertEqual([method for method, _, _ in self.requests], ["HEAD", "GET", "GET", "GET", "GET"])
        self.assertEqual([row[2].get("Range") for row in self.requests[1:]],
                         ["bytes=0-1023", "bytes=4608-5631", "bytes=9216-10239", "bytes=10240-10240"])
        self.assertTrue(all(row[2]["Origin"] == self.inventory["appOrigin"] for row in self.requests))
        self.assertTrue(all("Authorization" not in row[2] and "Cookie" not in row[2] for row in self.requests))

    def test_invalid_delivery_responses_are_rejected(self):
        for mode in ("ignores-range", "wrong-total", "wrong-start", "short-body", "wrong-bytes",
                     "oversized-body", "missing-length", "wrong-type", "no-cache", "missing-cors",
                     "encoded-range", "wrong-416", "unexposed-range"):
            with self.subTest(mode=mode):
                self.mode = mode
                result = self.verify()
                self.assertFalse(result["deliveryVerified"], result)
                self.assertTrue(result["assets"][0]["errors"])
                self.assertLessEqual(result["downloadedBytes"], 3 * 1025)

    def test_redirect_is_not_followed_or_disclosed(self):
        self.inventory["assets"][0]["url"] = self.origin + "/redirect?signature=top-secret"
        result = self.verify()
        self.assertFalse(result["deliveryVerified"])
        self.assertEqual(len(self.requests), 1)
        report = json.dumps(result)
        self.assertNotIn("top-secret", report)
        self.assertNotIn("credential=hidden", report)

    def test_allowlist_and_loopback_are_explicit_and_checked_before_io(self):
        for allowed, loopback in (([], True), ([self.origin], False)):
            with self.subTest(allowed=allowed, loopback=loopback):
                with self.assertRaises(delivery.InventoryError):
                    delivery.verify_inventory(self.inventory, allow_origins=allowed, allow_loopback=loopback)
                self.assertEqual(self.requests, [])

    def test_manifest_rejects_untrusted_fields_before_io(self):
        for field, value in (("bytes", True), ("bytes", 0), ("sha256", "bad"),
                             ("immutable", False), ("url", "http://user:secret@127.0.0.1/audio.mp3")):
            with self.subTest(field=field):
                original = self.inventory["assets"][0][field]
                self.inventory["assets"][0][field] = value
                with self.assertRaises(delivery.InventoryError):
                    self.verify()
                self.inventory["assets"][0][field] = original
                self.assertEqual(self.requests, [])

    def test_wrong_sample_window_rejects_before_io(self):
        self.inventory["assets"][0]["samples"][1]["start"] += 1
        with self.assertRaises(delivery.InventoryError):
            self.verify()
        self.assertEqual(self.requests, [])

    def test_missing_sample_proofs_cannot_pass_without_full_hash(self):
        del self.inventory["assets"][0]["samples"]
        result = self.verify()
        self.assertFalse(result["deliveryVerified"])
        self.assertIn("sample", " ".join(result["assets"][0]["errors"]).lower())

    def test_full_hash_is_explicit_and_budgeted_before_io(self):
        with self.assertRaises(delivery.InventoryError):
            self.verify(full_sha256=True)
        with self.assertRaises(delivery.InventoryError):
            self.verify(full_sha256=True, byte_budget=len(self.payload))
        self.assertEqual(self.requests, [])
        result = self.verify(full_sha256=True, byte_budget=20_000)
        self.assertTrue(result["deliveryVerified"], result)
        self.assertEqual(result["downloadedBytes"], len(self.payload) + 3072)
        self.assertTrue(result["assets"][0]["fullSha256Verified"])

    def test_full_hash_proves_absent_samples_and_rejects_corrupted_object(self):
        del self.inventory["assets"][0]["samples"]
        self.assertTrue(self.verify(full_sha256=True, byte_budget=20_000)["deliveryVerified"])
        self.mode = "wrong-full-bytes"
        self.assertFalse(self.verify(full_sha256=True, byte_budget=20_000)["deliveryVerified"])

    def test_small_global_budget_rejects_entire_inventory_before_io(self):
        with self.assertRaises(delivery.InventoryError):
            self.verify(byte_budget=10)
        self.assertEqual(self.requests, [])

    def test_boolean_schema_version_rejects_before_io(self):
        self.inventory["schemaVersion"] = True
        with self.assertRaises(delivery.InventoryError):
            self.verify()
        self.assertEqual(self.requests, [])

    def test_missing_media_origins_and_origin_path_reject_before_io(self):
        for value in ([], [self.origin + "/path"]):
            with self.subTest(value=value):
                self.inventory["mediaOrigins"] = value
                with self.assertRaises(delivery.InventoryError):
                    self.verify()
                self.assertEqual(self.requests, [])

    def test_total_budget_covers_all_assets_before_first_request(self):
        other = dict(self.inventory["assets"][0])
        other["url"] = self.origin + "/second.mp3"
        self.inventory["assets"].append(other)
        with self.assertRaises(delivery.InventoryError):
            self.verify(byte_budget=4000)
        self.assertEqual(self.requests, [])

    def test_no_etag_sha256_assumption(self):
        result = self.verify()
        self.assertTrue(result["deliveryVerified"], result)
        self.assertEqual(result["assets"][0]["metadata"]["etag"], '"a-provider-specific-etag"')

    def test_duplicate_cors_origin_headers_reject_even_when_each_value_is_allowed(self):
        for values in (("https://reader.example.com", "*"),
                       ("https://reader.example.com", "https://reader.example.com"),
                       ("https://reader.example.com, *",)):
            with self.subTest(values=values):
                self.cors_origins = values
                result = self.verify()
                self.assertFalse(result["deliveryVerified"], result)
                self.assertEqual(result["downloadedBytes"], 0)

    def test_all_cache_lines_and_parameterized_restrictions_are_checked(self):
        for restrictive in ("no-store", "no-cache", "private", 'no-cache="ETag"',
                            'private="Content-Type"', 'No-Store = "anything"'):
            with self.subTest(restrictive=restrictive):
                self.cache_headers = ["public, max-age=31536000, immutable", restrictive]
                result = self.verify()
                self.assertFalse(result["deliveryVerified"], result)
                self.assertEqual(result["downloadedBytes"], 0)

    def test_compatible_split_cache_headers_combine_into_the_recorded_policy(self):
        self.cache_headers = ["public", "max-age=31536000, immutable"]
        result = self.verify()
        self.assertTrue(result["deliveryVerified"], result)
        self.assertEqual(result["assets"][0]["metadata"]["cacheControl"],
                         "public, max-age=31536000, immutable")

    def test_conflicting_duplicate_max_age_cannot_hide_a_zero_lifetime(self):
        self.cache_headers = ["public, max-age=31536000, immutable", "max-age=0"]
        self.assertFalse(self.verify()["deliveryVerified"])


if __name__ == "__main__":
    unittest.main()
