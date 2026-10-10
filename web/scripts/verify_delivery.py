#!/usr/bin/env python3
"""Read-only, byte-budgeted verification of a private release upload inventory."""

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


SAMPLE_BYTES = 1024
DEFAULT_BYTE_BUDGET = 1024 * 1024
MIN_CACHE_SECONDS = 86400
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class InventoryError(ValueError):
    pass


class DeliveryError(ValueError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even an otherwise allowed redirect changes the verified object URL.
        return None


def sample_ranges(size):
    if type(size) is not int or size <= 0:
        raise InventoryError("Asset bytes must be a positive integer")
    width = min(SAMPLE_BYTES, size)
    return [(offset, offset + width - 1)
            for offset in (0, (size - width) // 2, size - width)]


def origin_of(url, origin_only=False):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        raise InventoryError("URLs must be nonempty and contain no whitespace")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise InventoryError("Invalid URL") from None
    if not parsed.netloc.isascii():
        raise InventoryError("URL authority must use ASCII host and port syntax")
    if (("[" in parsed.netloc or "]" in parsed.netloc) and
            re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]*)?", parsed.netloc) is None):
        raise InventoryError("Bracketed URL authority must contain only an IPv6 host and optional port")
    if (parsed.scheme not in ("https", "http") or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise InventoryError("HTTP URLs must have a host and no userinfo or fragment")
    if origin_only and (parsed.path not in ("", "/") or parsed.query):
        raise InventoryError("Origins must not contain paths or query strings")
    if port is not None and not 0 < port < 65536:
        raise InventoryError("URL port must be a positive canonical port")
    host = parsed.hostname.lower()
    if not host.isascii() or "%" in host:
        raise InventoryError("URL hosts must use canonical ASCII or explicit punycode")
    if ":" in host:
        try:
            host = "[" + ipaddress.IPv6Address(host).compressed + "]"
        except ipaddress.AddressValueError:
            raise InventoryError("URL host must be a canonical IPv6 address") from None
    else:
        name = host.removesuffix(".")
        if len(name) > 253:
            raise InventoryError("URL DNS host exceeds its maximum length")
        try:
            host = str(ipaddress.IPv4Address(name))
        except ipaddress.AddressValueError:
            if (re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.?", host) is None or
                    any(not 0 < len(label) <= 63 or label.startswith("-") or label.endswith("-")
                        for label in name.split(".")) or
                    re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", name.split(".")[-1]) is not None):
                raise InventoryError("URL host must be canonical DNS or IPv4; legacy numeric forms are forbidden")
    default = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default else "")


def redacted_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def is_loopback(origin):
    host = urlsplit(origin).hostname.removesuffix(".")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def validate_inventory(inventory, allow_origins, allow_loopback, full_sha256, byte_budget):
    if (not isinstance(inventory, dict) or type(inventory.get("schemaVersion")) is not int or
            inventory["schemaVersion"] != 1):
        raise InventoryError("Expected upload inventory schemaVersion 1")
    app_origin = origin_of(inventory.get("appOrigin"), origin_only=True)
    if is_loopback(app_origin):
        if not allow_loopback:
            raise InventoryError("Loopback reader origins require explicit --allow-loopback")
    elif urlsplit(app_origin).scheme != "https":
        raise InventoryError("Remote reader origins must use HTTPS")
    media = inventory.get("mediaOrigins")
    assets = inventory.get("assets")
    if not isinstance(media, list) or not media or not isinstance(assets, list) or not assets:
        raise InventoryError("Inventory needs mediaOrigins and a nonempty assets array")
    if len(assets) > 1000:
        raise InventoryError("Verify at most 1000 assets per inventory")
    manifest_origins = {origin_of(value, origin_only=True) for value in media}
    explicitly_allowed = {origin_of(value, origin_only=True) for value in allow_origins}
    maximum_read = 0
    seen = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise InventoryError("Every asset must be an object")
        origin = origin_of(asset.get("url"))
        if origin not in manifest_origins or origin not in explicitly_allowed:
            raise InventoryError("Every asset origin must be in the inventory and explicit allowlist")
        if is_loopback(origin):
            if not allow_loopback:
                raise InventoryError("Loopback requests require explicit --allow-loopback")
        elif urlsplit(origin).scheme != "https":
            raise InventoryError("Remote media must use HTTPS")
        if asset["url"] in seen:
            raise InventoryError("Duplicate asset URL")
        seen.add(asset["url"])
        bounds = sample_ranges(asset.get("bytes"))
        if not isinstance(asset.get("sha256"), str) or not SHA256.fullmatch(asset["sha256"]):
            raise InventoryError("Every asset needs a lowercase SHA-256")
        if asset.get("immutable") is not True:
            raise InventoryError("Only immutable assets may be verified")
        if asset.get("contentType") not in ("audio/mpeg", "image/webp", "application/json"):
            raise InventoryError("Unsupported asset contentType")
        samples = asset.get("samples")
        if samples is not None:
            if not isinstance(samples, list) or len(samples) != 3:
                raise InventoryError("Expected three ordered sample proofs")
            for sample, (start, end) in zip(samples, bounds):
                if (not isinstance(sample, dict) or type(sample.get("start")) is not int or
                        type(sample.get("end")) is not int or sample["start"] != start or
                        sample["end"] != end or not isinstance(sample.get("sha256"), str) or
                        not SHA256.fullmatch(sample["sha256"])):
                    raise InventoryError("Invalid sample window or SHA-256")
        # One additional byte detects an origin returning too much content.
        maximum_read += sum(end - start + 2 for start, end in bounds)
        if full_sha256:
            maximum_read += asset["bytes"] + 1
    if full_sha256 and byte_budget is None:
        raise InventoryError("--full-sha256 requires an explicit --byte-budget")
    limit = DEFAULT_BYTE_BUDGET if byte_budget is None else byte_budget
    if type(limit) is not int or limit <= 0 or maximum_read > limit:
        raise InventoryError(f"Byte budget must cover worst-case reads ({maximum_read} bytes)")
    return app_origin, assets, limit


class Budget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def read(self, response, count):
        if count > self.limit - self.used:
            raise DeliveryError("Download byte budget exhausted")
        try:
            data = response.read(count)
        except http.client.IncompleteRead as exc:
            self.used += len(exc.partial)
            raise DeliveryError("Response ended before its promised Content-Length") from None
        self.used += len(data)
        return data


def require(condition, message):
    if not condition:
        raise DeliveryError(message)


def check_headers(response, asset, app_origin, minimum_cache_seconds):
    headers = response.headers
    require(headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == asset["contentType"],
            "Content-Type does not match inventory")
    require(headers.get("Content-Encoding", "identity").lower() == "identity", "Encoded media changes byte ranges")
    policy = ", ".join(headers.get_all("Cache-Control", []))
    directives = [value.strip().lower() for value in policy.split(",")]
    directive_names = {value.split("=", 1)[0].strip() for value in directives}
    require(not directive_names.intersection(("private", "no-store", "no-cache")),
            "Immutable delivery must permit public caching")
    max_ages = [value.split("=", 1)[1].strip().strip('"')
                for value in directives if "=" in value and value.split("=", 1)[0].strip() == "max-age"]
    require("public" in directives and len(max_ages) == 1 and max_ages[0].isdigit() and
            int(max_ages[0]) >= minimum_cache_seconds,
            "Immutable asset Cache-Control needs a sufficient public max-age")
    origins = headers.get_all("Access-Control-Allow-Origin", [])
    require(len(origins) == 1 and origins[0].strip() in (app_origin, "*"),
            "CORS needs exactly one valid allow-origin header for the reader")
    exposed = {value.strip().lower() for value in headers.get("Access-Control-Expose-Headers", "").split(",")}
    require("*" in exposed or {"content-range", "accept-ranges", "etag"}.issubset(exposed),
            "CORS does not expose range/validator headers")


def open_response(opener, url, method, app_origin, timeout, range_value=None):
    headers = {"Origin": app_origin, "Accept-Encoding": "identity"}
    if range_value is not None:
        headers["Range"] = range_value
    request = Request(url, method=method, headers=headers)
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        return exc
    except (URLError, OSError, http.client.HTTPException):
        # Do not echo URLs, signed query parameters, redirect targets, or proxy data.
        raise DeliveryError("Network request failed") from None


def verify_asset(asset, app_origin, opener, budget, full_sha256, timeout, minimum_cache_seconds):
    result = {"url": redacted_url(asset["url"]), "bytes": asset["bytes"],
              "sha256": asset["sha256"], "deliveryVerified": False,
              "sampleSha256Verified": False, "fullSha256Verified": False,
              "metadata": {}, "rangeChecks": [], "errors": []}
    try:
        with open_response(opener, asset["url"], "HEAD", app_origin, timeout) as response:
            require(response.status == 200, f"HEAD returned HTTP {response.status}; redirects are not followed")
            check_headers(response, asset, app_origin, minimum_cache_seconds)
            require(response.headers.get("Content-Length") == str(asset["bytes"]), "HEAD Content-Length does not match inventory")
            require(response.headers.get("Accept-Ranges", "").lower() == "bytes", "HEAD does not advertise byte ranges")
            result["metadata"] = {"contentType": response.headers.get("Content-Type"),
                                  "contentLength": asset["bytes"], "cacheControl": ", ".join(response.headers.get_all("Cache-Control", [])),
                                  "acceptRanges": response.headers.get("Accept-Ranges"), "etag": response.headers.get("ETag"),
                                  "corsOrigin": response.headers.get("Access-Control-Allow-Origin"),
                                  "corsExposeHeaders": response.headers.get("Access-Control-Expose-Headers"),
                                  "cacheStatus": response.headers.get("CF-Cache-Status") or response.headers.get("X-Vercel-Cache"),
                                  "age": response.headers.get("Age")}
        samples = asset.get("samples")
        for index, (start, end) in enumerate(sample_ranges(asset["bytes"])):
            with open_response(opener, asset["url"], "GET", app_origin, timeout, f"bytes={start}-{end}") as response:
                require(response.status == 206, f"Range GET returned HTTP {response.status}, expected 206")
                check_headers(response, asset, app_origin, minimum_cache_seconds)
                require(response.headers.get("Content-Range") == f"bytes {start}-{end}/{asset['bytes']}",
                        "Range Content-Range does not match requested bytes and total")
                require(response.headers.get("Content-Length") == str(end - start + 1), "Range Content-Length does not match interval")
                require(response.headers.get("ETag") == result["metadata"]["etag"], "ETag changed between HEAD and Range GET")
                body = budget.read(response, end - start + 2)
                require(len(body) == end - start + 1, "Range response has the wrong body length")
                digest = hashlib.sha256(body).hexdigest()
                if samples:
                    require(digest == samples[index]["sha256"], "Range bytes differ from approved source sample")
                result["rangeChecks"].append({"start": start, "end": end, "status": 206, "sha256": digest,
                                              "sampleVerified": bool(samples),
                                              "cacheStatus": response.headers.get("CF-Cache-Status") or response.headers.get("X-Vercel-Cache"),
                                              "age": response.headers.get("Age")})
        with open_response(opener, asset["url"], "GET", app_origin, timeout,
                           f"bytes={asset['bytes']}-{asset['bytes']}") as response:
            require(response.status == 416, f"Unsatisfiable Range returned HTTP {response.status}, expected 416")
            require(response.headers.get("Content-Range") == f"bytes */{asset['bytes']}", "416 Content-Range total does not match inventory")
            result["unsatisfiableRangeStatus"] = 416
            # An error body is irrelevant to audio seeking; close without downloading it.
        result["sampleSha256Verified"] = bool(samples)
        if full_sha256:
            with open_response(opener, asset["url"], "GET", app_origin, timeout) as response:
                require(response.status == 200, f"Full GET returned HTTP {response.status}, expected 200")
                check_headers(response, asset, app_origin, minimum_cache_seconds)
                require(response.headers.get("Content-Length") == str(asset["bytes"]), "Full GET Content-Length does not match inventory")
                require(response.headers.get("ETag") == result["metadata"]["etag"], "ETag changed during full GET")
                remaining, digest = asset["bytes"], hashlib.sha256()
                while remaining:
                    body = budget.read(response, min(65536, remaining))
                    require(bool(body), "Full GET ended before declared bytes")
                    digest.update(body)
                    remaining -= len(body)
                require(not budget.read(response, 1), "Full GET returned more bytes than inventory")
                require(digest.hexdigest() == asset["sha256"], "Full object SHA-256 does not match inventory")
                result["fullSha256Verified"] = True
        require(bool(samples) or result["fullSha256Verified"], "Sample integrity is unproven; add sample SHA-256 proofs or explicitly verify full SHA-256")
        result["deliveryVerified"] = True
    except (DeliveryError, OSError, http.client.HTTPException) as exc:
        result["errors"].append(str(exc) if isinstance(exc, DeliveryError) else "Response read failed")
    return result


def verify_inventory(inventory, allow_origins=(), allow_loopback=False, full_sha256=False,
                     byte_budget=None, timeout=15, minimum_cache_seconds=MIN_CACHE_SECONDS):
    app_origin, assets, limit = validate_inventory(inventory, allow_origins, allow_loopback, full_sha256, byte_budget)
    if type(minimum_cache_seconds) is not int or minimum_cache_seconds <= 0:
        raise InventoryError("minimum_cache_seconds must be a positive integer")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 60:
        raise InventoryError("timeout must be between 0 and 60 seconds")
    budget = Budget(limit)
    # No ambient cookies, authorization, proxy credentials, cache, or redirect following.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    results = [verify_asset(asset, app_origin, opener, budget, full_sha256, timeout, minimum_cache_seconds)
               for asset in assets]
    return {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(),
            "deliveryVerified": all(row["deliveryVerified"] for row in results),
            "accessPolicyVerified": False, "appOrigin": app_origin,
            "downloadedBytes": budget.used, "byteBudget": limit,
            "fullSha256Requested": bool(full_sha256), "assets": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path, help="Private release upload-inventory JSON")
    parser.add_argument("--allow-origin", action="append", default=[], help="Exact permitted media origin (repeat for each host)")
    parser.add_argument("--allow-loopback", action="store_true", help="Permit explicitly allowlisted localhost fixtures")
    parser.add_argument("--full-sha256", action="store_true", help="Explicitly download every whole object to prove its SHA-256")
    parser.add_argument("--byte-budget", type=int, help="Maximum total response bytes; mandatory with --full-sha256")
    parser.add_argument("--timeout", type=float, default=15, help="Per-request timeout, maximum 60 seconds")
    parser.add_argument("--min-cache-seconds", type=int, default=MIN_CACHE_SECONDS)
    args = parser.parse_args(argv)
    try:
        if args.inventory.stat().st_size > 2 * 1024 * 1024:
            raise InventoryError("Inventory exceeds 2 MiB")
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        result = verify_inventory(inventory, args.allow_origin, args.allow_loopback, args.full_sha256,
                                  args.byte_budget, args.timeout, args.min_cache_seconds)
    except (InventoryError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Invalid inventory or options: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["deliveryVerified"] else 1


if __name__ == "__main__":
    sys.exit(main())
