"""Small HTTP fixtures prove delivery without uploading or downloading the book."""

import hashlib
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import PropertyMock, patch


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

    def https_media_inventory(self, app_origin):
        return dict(self.inventory, appOrigin=app_origin, mediaOrigins=["https://media.example"],
                    assets=[dict(self.inventory["assets"][0], url="https://media.example/audio.mp3")])

    def test_remote_http_reader_is_rejected_before_any_network_even_with_https_media(self):
        inventory = self.https_media_inventory("http://reader.example.com")
        for allow_local in (False, True):
            with self.subTest(allow_loopback=allow_local), patch.object(delivery, "build_opener") as opened:
                with self.assertRaisesRegex(delivery.InventoryError, "HTTPS"):
                    delivery.verify_inventory(inventory, allow_origins=["https://media.example"],
                                              allow_loopback=allow_local)
                opened.assert_not_called()
                self.assertEqual(self.requests, [])

    def test_loopback_reader_requires_explicit_local_policy_with_https_media(self):
        for host in ("localhost", "localhost.", "127.0.0.1", "127.0.0.1.", "[::1]"):
            for scheme in ("http", "https"):
                inventory = self.https_media_inventory(scheme + "://" + host + ":8123")
                with self.subTest(host=host, scheme=scheme), patch.object(delivery, "build_opener") as opened:
                    with self.assertRaisesRegex(delivery.InventoryError, "allow-loopback|Loopback"):
                        delivery.verify_inventory(inventory, allow_origins=["https://media.example"])
                    opened.assert_not_called()
                    origin, _, _ = delivery.validate_inventory(inventory, ["https://media.example"], True, False, None)
                    canonical_host = "127.0.0.1" if host == "127.0.0.1." else host
                    self.assertEqual(origin, scheme + "://" + canonical_host + ":8123")
                    self.assertEqual(self.requests, [])

    def test_root_dot_loopback_media_preserves_explicit_allowlist_policy(self):
        for host in ("localhost.", "127.0.0.1."):
            origin = "http://" + host + ":8123"
            inventory = dict(self.inventory, mediaOrigins=[origin],
                             assets=[dict(self.inventory["assets"][0], url=origin + "/audio.mp3")])
            with self.subTest(host=host):
                with self.assertRaisesRegex(delivery.InventoryError, "allow-loopback"):
                    delivery.validate_inventory(inventory, [origin], False, False, None)
                self.assertEqual(delivery.validate_inventory(inventory, [origin], True, False, None)[0],
                                 inventory["appOrigin"])
                with self.assertRaisesRegex(delivery.InventoryError, "allowlist"):
                    delivery.validate_inventory(inventory, [], True, False, None)
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


class OriginPolicyTests(unittest.TestCase):
    def inventory(self):
        payload = b"delivery origin fixture"
        return {"schemaVersion": 1, "appOrigin": "https://reader.example", "mediaOrigins": ["https://media.example"],
                "assets": [{"url": "https://media.example/audio.mp3", "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(), "contentType": "audio/mpeg", "immutable": True,
                            "samples": [{"start": start, "end": end,
                                         "sha256": hashlib.sha256(payload[start:end + 1]).hexdigest()}
                                        for start, end in delivery.sample_ranges(len(payload))]}]}

    def test_ambiguous_ipv4_forms_reject_in_every_origin_field_before_network(self):
        for host in ("127.1", "127.0.1", "2130706433", "0x7f000001", "0177.0.0.1", "127.000.000.001", "0x7f.1"):
            for root_dot in ("", "."):
                origin = "https://" + host + root_dot
                for field in ("appOrigin", "mediaOrigins", "allowlist", "assetURL"):
                    for allow_local in (False, True):
                        with self.subTest(host=host, root_dot=root_dot, field=field, allow_local=allow_local):
                            inventory, allowed = self.inventory(), ["https://media.example"]
                            if field == "appOrigin":
                                inventory["appOrigin"] = origin
                            elif field == "mediaOrigins":
                                inventory["mediaOrigins"].append(origin)
                            elif field == "allowlist":
                                allowed.append(origin)
                            else:
                                inventory["mediaOrigins"] = allowed = [origin]
                                inventory["assets"][0]["url"] = origin + "/audio.mp3?signature=fixture"
                            with patch.object(delivery, "build_opener") as opened:
                                with self.assertRaisesRegex(delivery.InventoryError, "canonical|host"):
                                    delivery.verify_inventory(inventory, allow_origins=allowed, allow_loopback=allow_local)
                                opened.assert_not_called()

    def test_supported_hosts_have_canonical_origins_without_dns_resolution(self):
        for url, origin in [
            ("https://READER.example:443/audio.mp3", "https://reader.example"),
            ("https://reader.example./", "https://reader.example."),
            ("https://xn--mnich-kva.example:8443/media", "https://xn--mnich-kva.example:8443"),
            ("http://localhost.:80/media", "http://localhost."),
            ("https://127.0.0.1.:443/media", "https://127.0.0.1"),
            ("https://192.0.2.1:8443/media", "https://192.0.2.1:8443"),
            ("https://[0:0:0:0:0:0:0:1]:443/media", "https://[::1]"),
            ("https://[2001:0db8:0:0:0:0:0:1]/media", "https://[2001:db8::1]"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(delivery.origin_of(url), origin)

    def test_noncanonical_host_and_zero_port_reject_before_network(self):
        oversized = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 62])
        for origin in ("https://münich.example", "https://reader_name.example", "https://%31%32%37.0.0.1",
                       "https://[fe80::1%25en0]", "https://" + oversized, "https://" + "a" * 64 + ".example",
                       "https://reader.example:0", "https://reader.123", "https://reader.0x7f"):
            with self.subTest(origin=origin), patch.object(delivery, "build_opener") as opened:
                inventory = self.inventory();inventory["appOrigin"] = origin
                with self.assertRaises(delivery.InventoryError):
                    delivery.verify_inventory(inventory, allow_origins=["https://media.example"], allow_loopback=True)
                opened.assert_not_called()

    def test_ipv4_mapped_loopback_requires_local_permission_for_reader_and_media(self):
        origin = "https://[::ffff:7f00:1]"
        for field in ("reader", "media"):
            inventory, allowed = self.inventory(), ["https://media.example"]
            if field == "reader":
                inventory["appOrigin"] = origin
            else:
                inventory["mediaOrigins"] = allowed = [origin]
                inventory["assets"][0]["url"] = origin + "/audio.mp3"
            with self.subTest(field=field), patch.object(delivery, "build_opener") as opened:
                with self.assertRaisesRegex(delivery.InventoryError, "allow-loopback"):
                    delivery.verify_inventory(inventory, allow_origins=allowed)
                opened.assert_not_called()
                self.assertEqual(delivery.validate_inventory(inventory, allowed, True, False, None)[0],
                                 inventory["appOrigin"])

    def test_mapped_ipv6_cors_origin_is_portable_when_platform_compresses_to_dotted_ipv4(self):
        for address, dotted, origin in [
            ("::ffff:7f00:1", "::ffff:127.0.0.1", "https://[::ffff:7f00:1]"),
            ("::ffff:c000:201", "::ffff:192.0.2.1", "https://[::ffff:c000:201]"),
            ("::ffff:0:0", "::ffff:0.0.0.0", "https://[::ffff:0:0]"),
        ]:
            for host in (address, dotted):
                with self.subTest(host=host), \
                        patch.object(delivery.ipaddress.IPv6Address, "compressed", new_callable=PropertyMock, return_value=dotted):
                    raw_origin = "https://[" + host + "]:443"
                    self.assertEqual(delivery.origin_of(raw_origin + "/audio.mp3"), origin)
                    inventory = self.inventory()
                    inventory["appOrigin"] = raw_origin
                    inventory["mediaOrigins"] = [raw_origin]
                    inventory["assets"][0]["url"] = raw_origin + "/audio.mp3"
                    app, assets, _ = delivery.validate_inventory(inventory, [raw_origin], True, False, None)
                    self.assertEqual(app, origin)
                    self.assertEqual(delivery.origin_of(assets[0]["url"]), origin)

    def test_browser_invalid_authorities_cannot_normalize_into_an_allowlisted_origin(self):
        for malformed, canonical in [
            ("https://media.example:０８０", "https://media.example:80"),
            ("https://[v1.media.example]", "https://v1.media.example"),
            ("https://prefix[::1]", "https://[::1]"),
            ("https://[::1]suffix", "https://[::1]"),
        ]:
            for field in ("appOrigin", "mediaOrigins", "allowlist", "assetURL"):
                with self.subTest(origin=malformed, field=field), patch.object(delivery, "build_opener") as opened:
                    inventory, allowed = self.inventory(), ["https://media.example"]
                    if field == "appOrigin":
                        inventory["appOrigin"] = malformed
                    elif field == "mediaOrigins":
                        inventory["mediaOrigins"].append(malformed)
                    elif field == "allowlist":
                        allowed.append(malformed)
                    else:
                        inventory["mediaOrigins"] = allowed = [canonical]
                        inventory["assets"][0]["url"] = malformed + "/audio.mp3"
                    with self.assertRaises(delivery.InventoryError):
                        delivery.verify_inventory(inventory, allow_origins=allowed, allow_loopback=True)
                    opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
