#!/usr/bin/env python3
"""Prepare approved reader assets privately; this command never uploads them."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import book_content as content


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def safe_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]*", value) is not None


def visible_text(value, name):
    content.require(isinstance(value, str) and 0 < len(value.strip()) <= 500
                    and not re.search(r"[<>\x00-\x1f\x7f]", value), f"Unsafe or missing visible {name}")
    return value


def url_base(value, name):
    content.require(isinstance(value, str) and not re.search(r"[\\\s%]", value), f"Invalid {name} URL base")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise content.ContentError(f"Invalid {name} URL base: {exc}") from exc
    content.require(parsed.scheme == "https" and parsed.hostname and parsed.username is None
                    and parsed.password is None and not parsed.query and not parsed.fragment
                    and "?" not in value and "#" not in value
                    and all(segment not in {".", ".."} for segment in parsed.path.split("/")),
                    f"{name} requires an absolute HTTPS URL without credentials, query, fragment, or traversal")
    content.require(port is None or 0 < port < 65536, f"Invalid {name} port")
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + ipaddress.IPv6Address(host).compressed + "]"
    origin = "https://" + host + (":" + str(port) if port is not None and port != 443 else "")
    return origin + parsed.path.rstrip("/") + "/", origin


def validate_plan(plan, registry):
    content.require(isinstance(plan, dict) and plan.get("schemaVersion") == 1 and safe_id(plan.get("releaseId")),
                    "Invalid explicit release plan identity")
    content.require(plan.get("accessModel") in {"private", "public", "authenticated"}, "Explicit accessModel is required")
    app_base, app_origin = url_base(plan.get("appDataURLbase"), "appDataURLbase")
    media_base, media_origin = url_base(plan.get("mediaURLbase"), "mediaURLbase")
    visible_text(plan.get("author"), "author")
    narrators = plan.get("narrators")
    content.require(isinstance(narrators, list) and bool(narrators) and all(isinstance(row, dict)
                    and safe_id(row.get("id")) for row in narrators), "Invalid release narrators")
    narrator_ids = [row["id"] for row in narrators]
    content.require(len(set(narrator_ids)) == len(narrator_ids), "Duplicate release narrator")
    for row in narrators:
        visible_text(row.get("label"), "narrator label")
    selected = plan.get("tracks")
    content.require(isinstance(selected, list) and bool(selected) and all(isinstance(row, dict) for row in selected),
                    "Release must explicitly select tracks")
    ids = [row.get("id") for row in selected]
    canonical_ids = [track["id"] for track in registry["tracks"]]
    content.require(all(isinstance(track_id, str) and track_id in canonical_ids for track_id in ids)
                    and ids == [track_id for track_id in canonical_ids if track_id in ids],
                    "Release track selection is unknown, duplicated, or out of canonical order")
    extents = []
    for row in selected:
        required = row.get("requiredNarratorIds")
        content.require(isinstance(required, list) and bool(required) and all(narrator in narrator_ids for narrator in required)
                        and len(set(required)) == len(required), "Each track needs explicit required narrator IDs")
        extent = row.get("extentId", row["id"] + "-full")
        content.require(safe_id(extent) and extent not in registry["legacyAliases"],
                        "Unsafe or reused legacy full reading extent ID")
        extents.append(extent)
    content.require(len(set(extents)) == len(extents), "Duplicate full reading extent ID")
    content.require(plan.get("defaultTrackId") in ids and plan.get("defaultNarratorId") in narrator_ids,
                    "Release defaults must belong to the explicit selection")
    return app_base, media_base, app_origin, list(dict.fromkeys([app_origin, media_origin]))


def scene_content_hash(scene):
    """Bind the reviewed painting and reveal text to its canonical trigger."""
    return content.object_hash({key: scene.get(key) for key in ["id", "sentenceId", "sha256", "title", "alt"]})


def scene_approval_valid(scene):
    review = scene.get("approval")
    if not isinstance(review, dict) or review.get("status") not in {"approved", "accepted-with-note"}:
        return False
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        return False
    if review["status"] == "accepted-with-note" and (not isinstance(review.get("note"), str) or not review["note"].strip()):
        return False
    try:
        timestamp = datetime.fromisoformat(review.get("reviewedAt", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return False
    return timestamp.tzinfo is not None and review.get("sceneSha256") == scene_content_hash(scene)


def prepare_scenes(root, scenes, media_base):
    content.require(isinstance(scenes, list) and all(isinstance(scene, dict) for scene in scenes), "Invalid reviewed scene list")
    public, files = [], []
    for scene in scenes:
        content.require(safe_id(scene.get("id")) and content.valid_hash(scene.get("sha256")), "Invalid reviewed scene identity/hash")
        visible_text(scene.get("title"), "scene title")
        visible_text(scene.get("alt"), "scene alt")
        content.require(scene_approval_valid(scene), "Missing/stale scene painting and reveal approval")
        content.require(isinstance(scene.get("sourcePath"), str), "Missing scene sourcePath")
        path = content.project_file(root, scene["sourcePath"])
        extension = path.suffix.lower().lstrip(".")
        content.require(extension == "webp", "Reader scenes require optimized WebP paintings")
        content_type = "image/webp"
        content.require(content.file_hash(path) == scene["sha256"], "Scene image hash differs")
        url = media_base + "images/" + scene["sha256"] + "." + extension
        public.append({"id": scene["id"], "sentenceId": scene["sentenceId"], "src": url,
                       "title": scene["title"], "alt": scene["alt"], "description": scene["alt"]})
        files.append((url, path, scene["sha256"], content_type))
    return public, files


def range_samples(path):
    size = path.stat().st_size
    content.require(size > 0, "Empty upload asset")
    width = min(1024, size)
    result = []
    with path.open("rb") as stream:
        for start in [0, (size - width) // 2, size - width]:
            stream.seek(start)
            data = stream.read(width)
            content.require(len(data) == width, "Upload asset changed while sampling")
            result.append({"start": start, "end": start + width - 1, "sha256": hash_bytes(data)})
    content.require(path.stat().st_size == size, "Upload asset size changed while sampling")
    return result


def upload_entry(url, path, digest, content_type):
    return {"url": url, "sha256": digest, "bytes": path.stat().st_size, "contentType": content_type,
            "immutable": True, "samples": range_samples(path), "sourcePath": str(path)}


def current_inventory(root, registry, inventory):
    """Reconcile current metadata; never trust cached verification flags."""
    content.require(isinstance(inventory, dict), "Missing current recording inventory")
    for name, digest in registry["sourceHashes"].items():
        content.require(content.project_file_hash(root, Path("Draft") / registry["manuscriptVersion"] / name) == digest,
                        "Current manuscript source hash differs")
    manifests = [content.read_project_json(root, Path("Audiobook") / edition / "generation-manifest.json")
                 for edition in ["v7", "v8"]]
    canonical = content.build_registry(root / "Draft" / registry["manuscriptVersion"], manifests,
                                       manuscript_version=registry["manuscriptVersion"],
                                       expected_chapters=len(registry["tracks"]) - 2)
    def canonical_view(track):
        result = {key: track.get(key) for key in ["id", "kind", "number", "title", "part", "bodyMarkdown", "blocks",
                                                 "narrationSha256", "spokenIntroduction", "sectionHeadingMarkdown"]}
        result["paragraphs"] = []
        for paragraph in track["paragraphs"]:
            prose = {key: paragraph.get(key) for key in ["id", "markdown", "text", "emphasis", "headingLevel"]}
            prose["sentences"] = [{key: sentence[key] for key in ["id", "text", "textStart", "textEnd"]}
                                  for sentence in paragraph["sentences"]]
            result["paragraphs"].append(prose)
        return result
    content.require([canonical_view(track) for track in registry["tracks"]]
                    == [canonical_view(track) for track in canonical["tracks"]],
                    "Registry prose/structure differs from current canonical source")
    pilot_path = root / "web/data/chapter-001.json"
    if pilot_path.exists():
        pilot = content.read_project_json(root, pilot_path)
        content.require(registry["legacyAliases"].get(pilot.get("id")) == content.pilot_aliases(canonical, pilot),
                        "Registry legacy aliases differ from current pilot source")
    fresh = content.load_recording_inventory(root, registry)
    content.require(all(inventory.get(key) == fresh[key] for key in ["schemaVersion", "bookId", "registrySha256"]),
                    "Inventory registry binding differs")
    rows = inventory.get("recordings")
    content.require(isinstance(rows, list) and len(rows) == len(fresh["recordings"])
                    and all(isinstance(row, dict) for row in rows), "Inventory recording set differs")
    ignored = {"audioHashVerified", "audioVerificationSha256", "rawSourceHashesVerified", "rawSourceVerificationSha256", "contentApproval",
               "timingApproval", "publicationStatus"}
    for prior, current in zip(rows, fresh["recordings"]):
        content.require(all(prior.get(key) == value for key, value in current.items() if key not in ignored),
                        "Cached inventory differs from current production evidence")
    return {(row["trackId"], row["narratorId"]): row for row in fresh["recordings"]}


def part_titles(registry):
    result = {}
    for track in registry["tracks"]:
        match = re.search(r"^Part (One|Two|Three|Four|Five)\. ([^.]+)\.", track.get("spokenIntroduction", ""))
        if match:
            result[track["part"]] = "Part " + match.group(1) + " · " + match.group(2)
    return result


def validate_aliases(registry):
    """Pass supported legacy mappings unchanged without copying private fields."""
    aliases = registry.get("legacyAliases")
    content.require(isinstance(aliases, dict), "Invalid legacy alias mapping")
    tracks = {track["id"]: track for track in registry["tracks"]}
    for extent, alias in aliases.items():
        content.require(safe_id(extent) and isinstance(alias, dict)
                        and set(alias) == {"trackId", "paragraphIds", "sentenceIds", "completedExcerpt"}
                        and isinstance(alias["trackId"], str) and alias["trackId"] in tracks, "Invalid legacy alias record")
        track = tracks[alias["trackId"]]
        for key, ids in [("paragraphIds", {paragraph["id"] for paragraph in track["paragraphs"]}),
                         ("sentenceIds", {sentence["id"] for sentence in content.sentences(track)})]:
            mapping = alias[key]
            content.require(isinstance(mapping, dict) and bool(mapping)
                            and all(safe_id(old) and isinstance(new, str) and new in ids for old, new in mapping.items()),
                            "Invalid legacy alias anchor mapping")
        end = alias["completedExcerpt"]
        content.require(isinstance(end, dict) and set(end) == {"sentenceId", "sentenceFraction", "trackCompleted"}
                        and isinstance(end["sentenceId"], str) and end["sentenceId"] in alias["sentenceIds"].values()
                        and content.finite_number(end["sentenceFraction"]) and 0 <= end["sentenceFraction"] <= 1
                        and end["trackCompleted"] is False, "Invalid legacy alias excerpt completion")


def reader_payload(registry, track, recording, timing, track_plan, plan, audio_url, scenes, part_title):
    cues = {cue["sentenceId"]: cue for cue in timing["sentences"]}
    paragraphs = []
    for paragraph in track["paragraphs"]:
        public = {key: copy.deepcopy(paragraph[key]) for key in ["id", "markdown", "text", "emphasis"]}
        if "headingLevel" in paragraph:
            public["headingLevel"] = paragraph["headingLevel"]
        public["sentences"] = []
        for sentence in paragraph["sentences"]:
            cue = cues[sentence["id"]]
            row = dict({key: sentence[key] for key in ["id", "text", "textStart", "textEnd"]}, start=cue["start"], end=cue["end"])
            if timing["schemaVersion"] == 2:
                row["syncStatus"] = cue["syncStatus"]
            public["sentences"].append(row)
        measured = [row for row in public["sentences"] if content.finite_number(row["start"]) and content.finite_number(row["end"])]
        public.update(start=measured[0]["start"] if measured else None, end=measured[-1]["end"] if measured else None)
        paragraphs.append(public)
    resolved = content.resolve_scenes(track, recording, timing, scenes)
    for index, scene in enumerate(resolved):
        scene["end"] = resolved[index + 1]["start"] if index + 1 < len(resolved) else recording["decodedDuration"]
    extent = track_plan.get("extentId", track["id"] + "-full")
    label = next(row["label"] for row in plan["narrators"] if row["id"] == recording["narratorId"])
    return {"schemaVersion": timing["schemaVersion"], "bookId": registry["bookId"], "book": "LUMEN",
            "manuscriptVersion": registry["manuscriptVersion"], "chapterId": track["id"],
            "id": extent, "readingExtentId": extent, "readingExtent": "full", "title": track["title"],
            "author": plan["author"], "kind": track["kind"], "chapter": track["number"],
            "part": track["part"], "partTitle": part_title, "duration": recording["decodedDuration"],
            "start": 0.0, "end": recording["decodedDuration"], "narration": "AI-generated Gemini voice, " + label,
            "audio": {"src": audio_url, "sha256": recording["sha256"], "narratorId": recording["narratorId"],
                      "duration": recording["decodedDuration"], "decodedDuration": recording["decodedDuration"],
                      "bytes": recording["bytes"], "format": "audio/mpeg"},
            "paragraphs": paragraphs, "blocks": copy.deepcopy(track["blocks"]), "scenes": resolved}


def build_release(root, registry, inventory, timing_maps, scenes_by_track, plan, staging):
    """Validate an explicit subset, freshly verify bytes, and stage immutable JSON.

    This is preparation only. Access control and remote promotion are separate.
    """
    root, staging = Path(root).resolve(), Path(staging).resolve()
    public_root = (root / "web/dist").resolve()
    content.require(staging != public_root and public_root not in staging.parents,
                    "Release assets require private staging outside public web/dist")
    content.validate_registry(registry)
    validate_aliases(registry)
    app_base, media_base, app_origin, media_origins = validate_plan(plan, registry)
    content.require(isinstance(timing_maps, dict) and isinstance(scenes_by_track, dict), "Missing reviewed timing/scene mappings")
    recordings = current_inventory(root, registry, inventory)
    tracks = {track["id"]: track for track in registry["tracks"]}
    titles = part_titles(registry)
    for selected in plan["tracks"]:
        track_timings = timing_maps.get(selected["id"], {})
        content.require(isinstance(track_timings, dict), "Invalid track timing mapping")
        for narrator in selected["requiredNarratorIds"]:
            content.require((selected["id"], narrator) in recordings, "Missing required recording")
            content.require(narrator in track_timings, "Missing required reviewed timing map")
    manifest = {"schemaVersion": 1, "bookId": registry["bookId"], "manuscriptVersion": registry["manuscriptVersion"],
                "releaseId": plan["releaseId"], "defaultTrackId": plan["defaultTrackId"],
                "defaultNarratorId": plan["defaultNarratorId"],
                "narrators": [{"id": row["id"], "label": row["label"]} for row in plan["narrators"]],
                "tracks": [], "legacyAliases": copy.deepcopy(registry["legacyAliases"]),
                "appOrigin": app_origin, "mediaOrigins": media_origins}
    prepared, uploads = [], {}
    for selected in plan["tracks"]:
        track = tracks[selected["id"]]
        scene_data, scene_files = prepare_scenes(root, scenes_by_track.get(track["id"], []), media_base)
        row = {"id": track["id"], "title": track["title"], "part": track["part"],
               "partTitle": titles.get(track["part"]), "kind": track["kind"], "recordings": {}}
        for narrator in plan["narrators"]:
            narrator_id = narrator["id"]
            recording = recordings.get((track["id"], narrator_id))
            timing = timing_maps.get(track["id"], {}).get(narrator_id)
            if recording is None:
                content.require(timing is None, "Reviewed timing has no current recording")
                row["recordings"][narrator_id] = {"status": "unavailable"}
                continue
            if timing is None:
                row["recordings"][narrator_id] = {"status": "pending"}
                continue
            content.validate_timing_map(track, recording, timing)
            approvals = timing.get("approvals")
            content.require(isinstance(approvals, dict) and content.approval_valid(approvals.get("content"), recording, track),
                            "Missing/stale human content approval")
            alignment = approvals.get("alignment", {})
            proof = None
            if timing["schemaVersion"] == 2 and isinstance(alignment, dict) and alignment.get("status") == "verified":
                from finalize_release_timings import verify_technical_alignment
                verified, proof = verify_technical_alignment(root, track, recording, timing)
            else:
                content.require(content.approval_valid(alignment, recording, track)
                                and alignment.get("timingSha256") == content.timing_content_hash(timing),
                                "Missing/stale human timing or alignment approval")
                verified = content.verify_recording_sources(content.verify_recording_file(recording, root), root)
            content.validate_timing_map(track, verified, timing, require_approved=True, alignment_verification=proof)
            audio_url = media_base + "audio/" + narrator_id + "/" + track["id"] + "." + verified["sha256"] + ".mp3"
            payload = reader_payload(registry, track, verified, timing, selected, plan, audio_url, scene_data, row["partTitle"])
            digest = content.object_hash(payload)
            relative = "tracks/" + track["id"] + "/reader-" + narrator_id + "." + digest + ".json"
            row["recordings"][narrator_id] = {"status": "ready", "url": app_base + relative, "audioSha256": verified["sha256"]}
            prepared.append((track["id"], narrator_id, payload, app_base + relative))
            audio_path = content.project_file(root, recording["sourcePath"])
            uploads[audio_url] = upload_entry(audio_url, audio_path, verified["sha256"], "audio/mpeg")
        content.require(any(recording["status"] == "ready" for recording in row["recordings"].values()),
                        "Selected track has no approved ready recording")
        for url, path, digest, content_type in scene_files:
            uploads[url] = upload_entry(url, path, digest, content_type)
        manifest["tracks"].append(row)
    default = next(row for row in manifest["tracks"] if row["id"] == manifest["defaultTrackId"])
    content.require(default["recordings"][manifest["defaultNarratorId"]]["status"] == "ready", "Release default recording is pending/unavailable")
    # Complete every content, review, lineage, and physical-byte check before writing.
    for track_id, narrator_id, payload, url in prepared:
        asset = content.export_asset(staging, track_id, "reader-" + narrator_id, payload)
        uploads[url] = upload_entry(url, staging / asset["path"], asset["sha256"], "application/json")
    manifest_asset = content.export_asset(staging, "chapter-000", "book-manifest", manifest)
    manifest_url = app_base + manifest_asset["path"]
    uploads[manifest_url] = upload_entry(manifest_url, staging / manifest_asset["path"], manifest_asset["sha256"], "application/json")
    upload_inventory = {"schemaVersion": 1, "releaseId": plan["releaseId"], "accessModel": plan["accessModel"],
                        "appOrigin": app_origin, "mediaOrigins": media_origins, "assets": list(uploads.values())}
    upload_asset = content.export_asset(staging, "chapter-000", "upload-inventory", upload_inventory)
    return {"manifest": manifest, "manifestURL": manifest_url, "manifestPath": str(staging / manifest_asset["path"]),
            "manifestSha256": manifest_asset["sha256"], "uploadInventory": upload_inventory,
            "uploadInventoryPath": str(staging / upload_asset["path"]), "publicationStatus": "staged"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--timings", type=Path, required=True, help="Track ID -> narrator ID -> reviewed map")
    parser.add_argument("--scenes", type=Path, help="Track ID -> reviewed scene list; omission means text-only")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Private staging; defaults to /private/tmp/lumen-release-<releaseId>")
    args = parser.parse_args()
    plan = content.read_json(args.plan)
    content.require(safe_id(plan.get("releaseId")), "Unsafe releaseId")
    staging = args.out or Path("/private/tmp") / ("lumen-release-" + plan["releaseId"])
    result = build_release(args.root, content.read_json(args.registry), content.read_json(args.inventory),
                           content.read_json(args.timings), content.read_json(args.scenes) if args.scenes else {}, plan, staging)
    print(json.dumps({key: result[key] for key in ["manifestURL", "manifestPath", "manifestSha256",
                                                  "uploadInventoryPath", "publicationStatus"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except content.ContentError as exc:
        raise SystemExit(f"Release export failed: {exc}")
