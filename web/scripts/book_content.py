#!/usr/bin/env python3
"""Build private, canonical Lumen release data without publishing unapproved audio.

This foundation reads existing manuscripts and production metadata. It never
changes a source file, creates narration, invents alignment, or grants approval.
The CLI exports to staging only; the current pilot runtime remains independent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
from datetime import datetime


class ContentError(ValueError):
    """A source or release asset fails the publication contract."""


def require(condition, message):
    if not condition:
        raise ContentError(message)


def json_bytes(value):
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ContentError(f"Invalid JSON asset: {exc}") from exc


def object_hash(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContentError(f"Cannot read JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"Expected JSON object: {path}")
    return value


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def spoken_comparison(text):
    """Comparison only: display text is never replaced by normalized narration."""
    text = text.replace("<short pause>", "")
    # These forms are the explicit v6 production substitutions in
    # Draft/v6/export_audiobook.py. Do not normalize arbitrary omissions away.
    for original, spoken in {"HEARTBEAT_FINAL": "Heartbeat Final", "sinoatrial_node": "sinoatrial node",
                             "sanity_check.py": "sanity check dot pie", "CTRL+ALT+DELETE": "Control, Alt, Delete",
                             "501(c)(4)": "five oh one, c four", ".onion": "dot onion"}.items():
        text = text.replace(original, spoken)
    def remaining(match):
        hours, minutes = map(int, match.groups())
        words = []
        if hours:
            words.append(number_words(hours) + (" hour" if hours == 1 else " hours"))
        if minutes:
            words.append(number_words(minutes) + (" minute" if minutes == 1 else " minutes"))
        return " and ".join(words) + " remaining" if words else "The trigger moment"
    text = re.sub(r"\bT-(\d{1,2}):(\d{2})\b", remaining, text)
    text = re.sub(r"\bT-minus (\d+) hours\b", lambda m: number_words(int(m.group(1))) + " hours remaining", text)
    return " ".join(text.split())


def display_markdown(markdown):
    """Retain original markdown and represent the manuscript's inline emphasis."""
    plain, emphasis, cursor = "", [], 0
    for match in re.finditer(r"\*\*[^*]+\*\*|\*[^*]+\*", markdown):
        plain += markdown[cursor:match.start()]
        width = 2 if match.group().startswith("**") else 1
        start = len(plain)
        plain += match.group()[width:-width]
        emphasis.append({"start": start, "end": len(plain), "kind": "bold" if width == 2 else "italic"})
        cursor = match.end()
    return plain + markdown[cursor:], emphasis


def sentence_ranges(text):
    # Dotted initialisms and personal initials can occur within prose before
    # either lower-case words (D.C. residence) or proper names (U.S. Supreme
    # Court, Ursula K. Le Guin). Clear sentence openers still end the sentence
    # after an initialism, as does the end of the paragraph.
    sentence_openers = {
        "A", "An", "The", "This", "That", "These", "Those", "He", "She", "It", "They", "We", "I", "You",
        "His", "Her", "Their", "Our", "My", "Your", "There", "Here", "Then", "But", "And", "Yet", "So",
        "Now", "When", "While", "After", "Before", "At", "In", "On", "As", "For", "From", "With", "Without",
        "By", "No", "Not", "Nothing", "Someone", "Something", "Everyone", "Everything", "Nobody", "Some",
        "Most", "All", "Each", "One", "Two", "Three", "Another", "Both", "Neither", "Either", "More", "Less",
        "Only", "Even", "Still", "Finally", "Meanwhile", "Later", "Next", "Instead", "Eventually",
    }
    cursor = 0
    for match in re.finditer(r"[.!?][\"”']?(?=\s+|$)", text):
        ending = match.end()
        candidate = text[cursor:ending].strip()
        if re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|et al|a\.m|p\.m|e\.g|i\.e)\.$", candidate, re.I):
            continue
        if re.search(r"\b(?:[A-Z]\.)+$", candidate.rstrip('\"”\'')):
            following = re.match(r'\s*[\"“‘(\[]*([A-Za-z]+)', text[ending:])
            if following and following.group(1) not in sentence_openers:
                continue
        if candidate:
            start = cursor + len(text[cursor:ending]) - len(text[cursor:ending].lstrip())
            yield start, ending
        cursor = ending
    if text[cursor:].strip():
        start = cursor + len(text[cursor:]) - len(text[cursor:].lstrip())
        yield start, len(text.rstrip())


def sentences(track):
    return [sentence for paragraph in track["paragraphs"] for sentence in paragraph["sentences"]]


def number_words(number):
    small = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
    if number < 20:
        return small[number]
    return tens[number // 10] + (" " + small[number % 10] if number % 10 else "")


def make_track(number, kind, body, version, part=None):
    track_id = f"chapter-{number:03d}"
    track = {"id": track_id, "kind": kind, "number": number if kind == "story" else None,
             "title": f"Chapter {number}" if kind == "story" else
                      ("Opening Credits and Epigraph" if kind == "opening-credits" else "Closing Credits"),
             "manuscriptVersion": version, "part": part, "bodyMarkdown": body,
             "paragraphs": [], "blocks": [], "publicationStatus": "pending"}
    for chunk in re.split(r"\n[ \t]*\n", body):
        if not chunk.strip():
            continue
        if re.fullmatch(r"\s*(?:-{3,}|\*{3,})\s*", chunk):
            track["blocks"].append({"kind": "scene-break", "markdown": chunk})
            continue
        index = len(track["paragraphs"]) + 1
        local_id = f"p{index:03d}"
        heading = re.fullmatch(r"(#{3,6})[ \t]+([^\n]+)", chunk)
        text, emphasis = display_markdown(heading.group(2) if heading else chunk)
        paragraph = {"id": f"{version}:{track_id}:{local_id}", "markdown": chunk,
                     "text": text, "emphasis": emphasis, "sentences": []}
        if heading:
            paragraph["headingLevel"] = len(heading.group(1))
        for sentence_index, (start, end) in enumerate(sentence_ranges(text), 1):
            paragraph["sentences"].append({"id": f"{version}:{track_id}:{local_id}-s{sentence_index:02d}",
                                            "text": text[start:end], "textStart": start, "textEnd": end})
        track["paragraphs"].append(paragraph)
        track["blocks"].append({"kind": "heading" if heading else "paragraph", "paragraphId": paragraph["id"]})
    require(bool(track["paragraphs"]), f"Empty canonical track: {track_id}")
    track["textSha256"] = object_hash({"id": track_id, "manuscriptVersion": version,
                                      "bodyMarkdown": body, "blocks": track["blocks"],
                                      "paragraphs": track["paragraphs"]})
    return track


def grouped_items(manifest, expected_chapters):
    require(isinstance(manifest, dict) and isinstance(manifest.get("items"), list), "Missing generation items")
    groups = {number: [] for number in range(expected_chapters + 2)}
    seen = set()
    previous = -1
    for item in manifest["items"]:
        number, item_id = item.get("chapter"), item.get("id")
        require(type(number) is int and number in groups and number >= previous,
                "Invalid generation track order")
        require(isinstance(item_id, str) and (item_id.startswith(f"chapter-{number:03d}-")
                or (number == expected_chapters + 1 and item_id == "closing-credits"))
                and item_id not in seen and isinstance(item.get("text"), str), "Invalid/duplicate generation item")
        previous = number
        seen.add(item_id)
        groups[number].append(item)
    require(all(groups.values()), "Missing generation track")
    return groups


def build_registry(manuscript_directory, generation_manifests, *, manuscript_version="v6",
                   expected_chapters=89, pilot=None, previous=None):
    """Verify both editions share canonical prose before assigning immutable IDs."""
    directory = Path(manuscript_directory)
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", manuscript_version) is not None,
            "Unsafe manuscript version")
    require(type(expected_chapters) is int and 0 < expected_chapters < 100, "Invalid chapter count")
    require(isinstance(generation_manifests, list) and bool(generation_manifests)
            and all(isinstance(manifest, dict) for manifest in generation_manifests),
            "Need source-bound generation manifest objects")
    reference = generation_manifests[0]
    hashes = reference.get("source_sha256")
    require(isinstance(hashes, dict) and bool(hashes), "Missing manuscript source hashes")
    require(all(isinstance(name, str) and re.fullmatch(r"part[1-9][0-9]*\.md", name) and valid_hash(digest)
                for name, digest in hashes.items()), "Invalid manuscript source hash")
    source_files = list(directory.glob("part*.md"))
    require({path.name for path in source_files} == set(hashes), "Manuscript source hash inventory differs")
    source_files.sort(key=lambda path: int(re.search(r"\d+", path.name).group()))
    for path in source_files:
        require(file_hash(path) == hashes[path.name], f"Manuscript source hash changed: {path.name}")
    selected = grouped_items(reference, expected_chapters)
    voices = set()
    for manifest in generation_manifests:
        require(manifest.get("source_sha256") == hashes, "Edition manuscript source hashes differ")
        voice = manifest.get("voice")
        require(isinstance(voice, str) and voice.strip() and voice not in voices, "Missing/duplicate narrator")
        voices.add(voice)
        grouped = grouped_items(manifest, expected_chapters)
        for number in selected:
            require([(i["id"], i["text"]) for i in grouped[number]] == [(i["id"], i["text"]) for i in selected[number]],
                    f"Edition narration differs: {voice}/chapter-{number:03d}")
    story = []
    for part_number, path in enumerate(source_files, 1):
        source = path.read_text(encoding="utf-8")
        headings = list(re.finditer(r"^## Chapter ([0-9]+)[ \t]*$", source, re.M))
        for index, heading in enumerate(headings):
            number = int(heading.group(1))
            end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
            body = source[heading.end():end].strip()
            section = re.search(r"^# EPILOGUE[^\n]*$", body, re.M)
            if section:
                body = body[:section.start()].rstrip()
            # The rule immediately preceding another chapter is its separator,
            # rather than a spoken scene break. Interior rules remain intact.
            body = re.sub(r"\n\n(?:-{3,}|\*{3,})$", "", body).rstrip()
            track = make_track(number, "story", body, manuscript_version, part_number)
            expected = spoken_comparison("\n\n".join(p["text"] + ("." if "headingLevel" in p else "")
                                                      for p in track["paragraphs"]))
            spoken = spoken_comparison("\n\n".join(i["text"] for i in selected.get(number, [])))
            require(spoken.endswith(expected), f"Generation does not preserve canonical prose: chapter-{number:03d}")
            prefix = spoken[:-len(expected)].strip()
            chapter_prefix = f"Chapter {number_words(number).title()}."
            require(prefix.endswith(chapter_prefix), f"Missing/mismatched spoken chapter heading: chapter-{number:03d}")
            part_prefix = prefix[:-len(chapter_prefix)].strip()
            require(not part_prefix or re.fullmatch(r"Part (?:One|Two|Three|Four|Five)\. [^.]+\.|Epilogue\. Daylight\.", part_prefix),
                    f"Unexpected spoken introduction: chapter-{number:03d}")
            track["spokenIntroduction"] = prefix
            before_heading = source[headings[index - 1].end() if index else 0:heading.start()]
            division = re.search(r"^# EPILOGUE[^\n]*$", before_heading, re.M)
            if division:
                track["sectionHeadingMarkdown"] = division.group()
            story.append(track)
    require([t["number"] for t in story] == list(range(1, expected_chapters + 1)),
            "Canonical chapters are missing, duplicated, or out of order")
    opening = make_track(0, "opening-credits", "\n\n".join(i["text"] for i in selected[0]), manuscript_version)
    closing = make_track(expected_chapters + 1, "closing-credits",
                         "\n\n".join(i["text"] for i in selected[expected_chapters + 1]), manuscript_version)
    for number, track in enumerate([opening] + story + [closing]):
        track["narrationSha256"] = object_hash([{ "id": item["id"], "text": item["text"]}
                                               for item in selected[number]])
    registry = {"schemaVersion": 1, "bookId": "lumen", "manuscriptVersion": manuscript_version,
                "sourceHashes": dict(hashes), "tracks": [opening] + story + [closing],
                "toc": [{"trackId": t["id"], "title": t["title"], "part": t["part"]} for t in story],
                "legacyAliases": {}, "publicationStatus": "pending"}
    if previous:
        validate_registry(previous)
        if previous["manuscriptVersion"] == manuscript_version:
            require(previous["sourceHashes"] == hashes, "Forbidden anchor reuse after source revision; create a new manuscript version and migration")
        old = {s["id"]: s["text"] for t in previous["tracks"] for s in sentences(t)}
        for track in registry["tracks"]:
            for sentence in sentences(track):
                require(sentence["id"] not in old or old[sentence["id"]] == sentence["text"],
                        f"Forbidden anchor reuse for changed prose: {sentence['id']}; create a new manuscript version and migration")
    if pilot is not None:
        registry["legacyAliases"][pilot.get("id", "")] = pilot_aliases(registry, pilot)
    validate_registry(registry)
    registry["registrySha256"] = object_hash(registry)
    return registry


def pilot_aliases(registry, pilot):
    require(pilot.get("id") == "chapter-001-pilot" and pilot.get("manuscriptVersion") == registry["manuscriptVersion"],
            "Unexpected pilot identity/manuscript")
    track = next((t for t in registry["tracks"] if t["id"] == pilot.get("chapterId")), None)
    require(track is not None, "Missing pilot canonical track")
    aliases, paragraphs, endpoint = {}, {}, None
    for index, paragraph in enumerate(pilot.get("paragraphs", [])):
        require(index < len(track["paragraphs"]), "Pilot contains unknown paragraph")
        canonical = track["paragraphs"][index]
        require(paragraph.get("id") == f"p{index + 1:03d}" and paragraph.get("text") == canonical["text"],
                "Pilot paragraph does not match canonical prose")
        require(len(paragraph.get("sentences", [])) == len(canonical["sentences"]), "Pilot sentence count differs")
        paragraphs[paragraph["id"]] = canonical["id"]
        for offset, (old, new) in enumerate(zip(paragraph["sentences"], canonical["sentences"]), 1):
            require(old.get("text") == new["text"] and old.get("id") == f"{paragraph['id']}-s{offset:02d}",
                    "Pilot sentence does not match canonical prose")
            aliases[old["id"]] = new["id"]
            endpoint = new["id"]
    require(endpoint is not None, "Pilot has no sentence anchors")
    return {"trackId": track["id"], "paragraphIds": paragraphs, "sentenceIds": aliases,
            "completedExcerpt": {"sentenceId": endpoint, "sentenceFraction": 1.0, "trackCompleted": False}}


def validate_registry(registry):
    require(isinstance(registry, dict), "Registry must be a JSON object")
    require(registry.get("schemaVersion") == 1 and registry.get("bookId") == "lumen", "Invalid registry identity")
    version = registry.get("manuscriptVersion")
    hashes = registry.get("sourceHashes")
    require(isinstance(version, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", version) is not None,
            "Invalid registry manuscript version")
    require(isinstance(hashes, dict) and bool(hashes) and all(isinstance(name, str)
            and re.fullmatch(r"part[1-9][0-9]*\.md", name) and valid_hash(value) for name, value in hashes.items()),
            "Invalid registry source hashes")
    tracks = registry.get("tracks", [])
    require(isinstance(tracks, list) and len(tracks) >= 3 and all(isinstance(t, dict) for t in tracks)
            and [t.get("id") for t in tracks] == [f"chapter-{i:03d}" for i in range(len(tracks))],
            "Invalid registry track order")
    require(tracks[0].get("kind") == "opening-credits" and tracks[-1].get("kind") == "closing-credits"
            and all(t.get("kind") == "story" for t in tracks[1:-1]), "Invalid registry track kinds")
    toc = registry.get("toc")
    require(isinstance(toc, list) and all(isinstance(item, dict) for item in toc)
            and [t.get("trackId") for t in toc] == [t["id"] for t in tracks[1:-1]], "Invalid story table of contents")
    ids = set()
    for track in tracks:
        require(track.get("manuscriptVersion") == registry["manuscriptVersion"], "Track manuscript mismatch")
        require(valid_hash(track.get("textSha256")) and isinstance(track.get("bodyMarkdown"), str)
                and isinstance(track.get("paragraphs"), list) and bool(track["paragraphs"])
                and all(isinstance(paragraph, dict) for paragraph in track["paragraphs"]), "Missing canonical track prose/hash")
        chunks = [chunk for chunk in re.split(r"\n[ \t]*\n", track["bodyMarkdown"]) if chunk.strip()]
        blocks = track.get("blocks")
        require(isinstance(blocks, list) and len(blocks) == len(chunks)
                and all(isinstance(block, dict) for block in blocks), "Invalid canonical blocks")
        paragraph_index = 0
        for block, chunk in zip(blocks, chunks):
            if re.fullmatch(r"\s*(?:-{3,}|\*{3,})\s*", chunk):
                require(block == {"kind": "scene-break", "markdown": chunk}, "Scene break reconstruction differs")
            else:
                require(paragraph_index < len(track["paragraphs"]), "Missing block paragraph")
                paragraph = track["paragraphs"][paragraph_index]
                require(isinstance(paragraph.get("id"), str) and isinstance(paragraph.get("markdown"), str), "Invalid canonical paragraph")
                heading = re.fullmatch(r"(#{3,6})[ \t]+([^\n]+)", chunk)
                require(paragraph["markdown"] == chunk and block == {"kind": "heading" if heading else "paragraph",
                        "paragraphId": paragraph["id"]}, "Canonical block reconstruction differs")
                require(paragraph.get("headingLevel") == (len(heading.group(1)) if heading else None),
                        "Canonical heading level differs")
                paragraph_index += 1
        require(paragraph_index == len(track["paragraphs"]), "Unreferenced canonical paragraph")
        for paragraph_index, paragraph in enumerate(track["paragraphs"], 1):
            require(paragraph["id"] not in ids and paragraph["id"] ==
                    f"{registry['manuscriptVersion']}:{track['id']}:p{paragraph_index:03d}", "Duplicate/unqualified paragraph anchor")
            ids.add(paragraph["id"])
            markdown = paragraph["markdown"]
            if "headingLevel" in paragraph:
                markdown = re.sub(r"^#{3,6}[ \t]+", "", markdown)
            text, emphasis = display_markdown(markdown)
            require(text == paragraph.get("text") and emphasis == paragraph.get("emphasis"), "Canonical display reconstruction differs")
            require(isinstance(paragraph.get("sentences"), list) and bool(paragraph["sentences"])
                    and all(isinstance(sentence, dict) and {"id", "text", "textStart", "textEnd"} <= set(sentence)
                            for sentence in paragraph["sentences"]), "Missing canonical sentences")
            cursor = 0
            for sentence_index, sentence in enumerate(paragraph["sentences"], 1):
                start, end = sentence["textStart"], sentence["textEnd"]
                require(isinstance(sentence["id"], str) and sentence["id"] not in ids
                        and sentence["id"] == paragraph["id"] + f"-s{sentence_index:02d}",
                        "Duplicate/unqualified sentence anchor")
                ids.add(sentence["id"])
                require(type(start) is int and type(end) is int and cursor <= start < end <= len(text)
                        and not text[cursor:start].strip() and text[start:end] == sentence["text"],
                        "Canonical sentence reconstruction differs")
                cursor = end
            require(cursor > 0 and not text[cursor:].strip(), "Canonical sentence coverage is incomplete")
        require(track["textSha256"] == object_hash({"id": track["id"], "manuscriptVersion": track["manuscriptVersion"],
                                                  "bodyMarkdown": track["bodyMarkdown"], "blocks": track["blocks"],
                                                  "paragraphs": track["paragraphs"]}), "Canonical text hash changed")
    if "registrySha256" in registry:
        require(valid_hash(registry["registrySha256"]) and registry["registrySha256"] ==
                object_hash({key: value for key, value in registry.items() if key != "registrySha256"}), "Registry hash changed")
    return True


def approval_valid(value, recording, track):
    if not isinstance(value, dict) or value.get("status") not in {"approved", "accepted-with-note"}:
        return False
    if not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip():
        return False
    try:
        reviewed_at = datetime.fromisoformat(value.get("reviewedAt", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return False
    return (reviewed_at.tzinfo is not None and value.get("audioSha256") == recording["sha256"]
            and value.get("textSha256") == track["textSha256"]
            and (value["status"] != "accepted-with-note" or
                 (isinstance(value.get("note"), str) and bool(value["note"].strip()))))


def timing_content_hash(timing):
    """Bind a review to every immutable map field, excluding reviewer metadata."""
    require(isinstance(timing, dict), "Timing must be a JSON object")
    return object_hash({key: value for key, value in timing.items() if key != "approvals"})


def validate_timing_map(track, recording, timing, *, require_approved=False):
    """Validate final encoded-file identity, complete anchors, and reviewed gates."""
    require(isinstance(timing, dict) and isinstance(recording, dict), "Timing and recording must be JSON objects")
    require(timing.get("schemaVersion") == 1 and timing.get("trackId") == recording.get("trackId") == track["id"],
            "Timing track identity differs")
    require(timing.get("manuscriptVersion") == track["manuscriptVersion"]
            and timing.get("textSha256") == track["textSha256"], "Timing canonical text hash differs")
    require(valid_hash(recording.get("sha256")) and timing.get("audioSha256") == recording["sha256"],
            "Timing final audio hash differs")
    require(timing.get("narratorId") == recording.get("narratorId") and bool(recording.get("narratorId")),
            "Timing narrator identity differs")
    duration = timing.get("duration")
    require(finite_number(duration) and duration > 0 and finite_number(recording.get("decodedDuration"))
            and abs(duration - recording["decodedDuration"]) < 0.001, "Timing decoded duration differs")
    cues = timing.get("sentences")
    require(isinstance(cues, list) and bool(cues) and all(isinstance(c, dict) for c in cues)
            and [c.get("sentenceId") for c in cues] == [s["id"] for s in sentences(track)],
            "Timing sentence anchors are missing, duplicated, unknown, or out of order")
    previous_end = 0.0
    for cue in cues:
        start, end = cue.get("start"), cue.get("end")
        require(finite_number(start) and finite_number(end) and previous_end <= start < end <= duration,
                "Timing cues overlap, are not finite, or exceed audio bounds")
        previous_end = end
    coverage = timing.get("coverage", {})
    require(isinstance(coverage, dict) and finite_number(coverage.get("introEnd"))
            and finite_number(coverage.get("tailStart")) and coverage["introEnd"] == cues[0]["start"]
            and coverage["tailStart"] == cues[-1]["end"],
            "Timing intro/tail coverage is missing or inconsistent")
    if require_approved:
        require(isinstance(timing.get("approvals"), dict), "Missing human approval records")
        for kind in ["content", "alignment"]:
            require(approval_valid(timing.get("approvals", {}).get(kind), recording, track),
                    f"Missing/stale human {kind} approval")
        require(timing["approvals"]["alignment"].get("timingSha256") == timing_content_hash(timing),
                "Missing/stale alignment timing content hash")
        require(recording.get("audioHashVerified") is True, "Final physical audio bytes are not hash verified")
    return True


def resolve_scenes(track, recording, timing, scenes):
    """Resolve shared reveal anchors using this voice's independently bound clock."""
    validate_timing_map(track, recording, timing)
    cues = {cue["sentenceId"]: cue for cue in timing["sentences"]}
    result, seen = [], set()
    previous = -1.0
    for scene in scenes:
        scene_id = scene.get("id")
        require(isinstance(scene_id, str) and scene_id and scene_id not in seen
                and scene.get("sentenceId") in cues and isinstance(scene.get("src"), str) and scene["src"],
                "Invalid scene identity, asset, or canonical trigger")
        seen.add(scene_id)
        start = cues[scene["sentenceId"]]["start"]
        require(start >= previous, "Canonical scene triggers are out of order")
        require("start" not in scene, "Shared scene must not contain a narrator timestamp")
        result.append(dict(scene, start=start))
        previous = start
    return result


def load_project_registry(root, *, previous=None):
    root = Path(root)
    manifests = [read_json(root / "Audiobook" / edition / "generation-manifest.json") for edition in ["v7", "v8"]]
    return build_registry(root / "Draft/v6", manifests, previous=previous,
                          pilot=read_json(root / "web/data/chapter-001.json"))


def load_recording_inventory(root, registry):
    """Reconcile 182 metadata-bound MP3s; physical hash verification stays pending.

    Delivery and QA hashes are checked, and each MP3's existence/size is checked.
    This does not rehash gigabytes or mistake package completion for approval.
    """
    root = Path(root).resolve()
    recordings = []
    for edition, narrator_id in [("v7", "autonoe"), ("v8", "charon")]:
        job = root / "Audiobook" / edition
        manifest_path = job / "generation-manifest.json"
        manifest = read_json(manifest_path)
        delivery = read_json(job / "delivery/delivery-manifest.json")
        require(manifest["source_sha256"] == registry["sourceHashes"], f"{edition}: inventory source hash differs")
        require(manifest["voice"].lower() == narrator_id and delivery.get("complete") is True,
                f"{edition}: incomplete delivery/narrator mismatch")
        require(delivery.get("generation_manifest_sha256") == file_hash(manifest_path), f"{edition}: stale delivery manifest")
        require(delivery.get("input_list_sha256") == file_hash(job / "chapters.json")
                and delivery.get("mastering_report_sha256") == file_hash(job / "mastered/mastering-report.json"),
                f"{edition}: stale chapter/mastering report")
        delivered = delivery.get("chapters", [])
        require([f"chapter-{row.get('key')}" for row in delivered] == [track["id"] for track in registry["tracks"]]
                and delivery.get("chapter_count") == len(registry["tracks"]), f"{edition}: inventory track order differs")
        selected = grouped_items(manifest, len(registry["tracks"]) - 2)
        for track, row in zip(registry["tracks"], delivered):
            require(track["narrationSha256"] == object_hash([{ "id": item["id"], "text": item["text"]}
                                                            for item in selected[int(row["key"])]]),
                    f"{edition}/{track['id']}: canonical narration identity differs")
            path = job / "mastered" / f"{track['id']}.mp3"
            qa_path = path.with_suffix(".qa.json")
            checkpoint_path = path.with_suffix(".checkpoint.json")
            qa, checkpoint = read_json(qa_path), read_json(checkpoint_path)
            binding = row.get("source_binding", {})
            require(path.resolve() == Path(row.get("file", "")).resolve() and path.is_file()
                    and path.stat().st_size == row.get("bytes"), f"{edition}/{track['id']}: MP3 path/size differs")
            require(valid_hash(row.get("sha256")) and checkpoint.get("output_sha256", {}).get("mp3") == row["sha256"]
                    and binding.get("output_sha256") == checkpoint["output_sha256"]
                    and binding.get("checkpoint_sha256") == file_hash(checkpoint_path)
                    and checkpoint["output_sha256"].get("qa") == file_hash(qa_path),
                    f"{edition}/{track['id']}: MP3/QA binding differs")
            samples = qa.get("decoded_mp3_qa", {}).get("samples")
            rate = qa.get("coordinator_checks", {}).get("sample_rate")
            require(type(samples) is int and samples > 0 and type(rate) is int and rate > 0,
                    f"{edition}/{track['id']}: missing decoded sample clock")
            recordings.append({"trackId": track["id"], "narratorId": narrator_id, "productionEdition": edition,
                               "sourcePath": str(path.relative_to(root)), "sha256": row["sha256"],
                               "bytes": row["bytes"], "decodedDuration": samples / rate,
                               "decodedSamples": samples, "sampleRate": rate,
                               "textSha256": track["textSha256"], "warnings": qa.get("warnings", []),
                               "publicationStatus": "pending", "contentApproval": "pending",
                               "timingApproval": "pending", "audioHashVerified": False})
    return {"schemaVersion": 1, "bookId": registry["bookId"], "registrySha256": registry["registrySha256"],
            "recordings": recordings, "publicationStatus": "pending"}


def verify_recording_file(recording, root):
    """Return verified metadata without mutating input or approving its content."""
    root = Path(root).resolve()
    path = (root / recording["sourcePath"]).resolve()
    require(root in path.parents and path.is_file() and path.stat().st_size == recording["bytes"],
            "Unexpected audio path/byte count")
    require(file_hash(path) == recording["sha256"], "Final audio file hash changed")
    return dict(recording, audioHashVerified=True)


def export_asset(destination, track_id, kind, value):
    """Atomically create immutable content-addressed JSON; identical exports skip."""
    require(re.fullmatch(r"chapter-[0-9]{3}", track_id) is not None
            and re.fullmatch(r"[a-z][a-z0-9-]*", kind) is not None, "Unsafe export track/kind")
    payload = json_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("tracks") / track_id / f"{kind}.{digest}.json"
    destination = Path(destination).resolve()
    path = destination / relative
    try:
        require(destination in path.resolve().parents, "Export descendant containment failed")
        child = destination
        for part in relative.parts:
            child /= part
            require(not child.is_symlink(), f"Export child symlink is forbidden: {child}")
    except (OSError, RuntimeError) as exc:
        raise ContentError(f"Cannot verify export containment: {exc}") from exc
    # Verify containment before creating any descendant. File-descriptor
    # traversal then prevents a concurrent symlink replacement from redirecting
    # a later mkdir, temporary write, or exclusive link outside this root.
    descriptors = []
    temporary = None
    try:
        destination.mkdir(parents=True, exist_ok=True)
        descriptors.append(os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, dir_fd=descriptors[-1])
            except FileExistsError:
                pass
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        parent_descriptor = descriptors[-1]
        def existing_bytes():
            try:
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW,
                                     dir_fd=parent_descriptor)
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                return stream.read()
        existing = existing_bytes()
        if existing is not None:
            require(existing == payload, f"Existing immutable asset changed: {relative}")
        else:
            temporary = ".export-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_descriptor)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, relative.name, src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor, follow_symlinks=False)
            except FileExistsError:
                require(existing_bytes() == payload, f"Existing immutable asset changed: {relative}")
    except OSError as exc:
        raise ContentError(f"Unsafe export symlink/path or failed immutable write: {exc}") from exc
    finally:
        if temporary is not None and descriptors:
            try:
                os.unlink(temporary, dir_fd=descriptors[-1])
            except FileNotFoundError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return {"path": relative.as_posix(), "sha256": digest, "bytes": len(payload)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", type=Path, help="Private staging directory; never web/dist")
    parser.add_argument("--previous", type=Path, help="Prior registry to reject silent anchor reuse")
    args = parser.parse_args()
    root = args.root.resolve()
    registry = load_project_registry(root, previous=read_json(args.previous) if args.previous else None)
    inventory = load_recording_inventory(root, registry)
    report = {"tracks": len(registry["tracks"]), "storyChapters": len(registry["toc"]),
              "sentences": sum(len(sentences(track)) for track in registry["tracks"]),
              "recordings": len(inventory["recordings"]), "publicationStatus": "pending",
              "registrySha256": registry["registrySha256"]}
    if args.out:
        destination = args.out.resolve()
        public = (root / "web/dist").resolve()
        require(destination != public and public not in destination.parents, "Pending exports must stay outside public web/dist")
        report["assets"] = [export_asset(destination, track["id"], "text", track) for track in registry["tracks"]]
        report["registry"] = export_asset(destination, "chapter-000", "registry", registry)
        report["inventory"] = export_asset(destination, "chapter-000", "inventory", inventory)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ContentError as exc:
        raise SystemExit(f"Content contract failed: {exc}")
