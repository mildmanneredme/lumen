#!/usr/bin/env python3
"""Select approved, unchanged 192 kbps Lumen audio and measured alignment evidence.

No uploads, regeneration, API calls, model downloads, or production mutations.
Local ASR is opt-in and uses only the already installed offline model.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from contextlib import contextmanager
import difflib
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import book_content as content

VERSION = "lumen-web-audio-1"
AUTHOR_APPROVAL = "Audio can go as is"
AUTHOR_NO_RETAKES = "No audio regen needed I checked a few and they are fine."
COMPARISON_SPEC = importlib.util.spec_from_file_location(
    "lumen_alignment_comparison", Path(__file__).resolve().parents[2] / "Audiobook/v6/check_audio_local.py")
COMPARISON = importlib.util.module_from_spec(COMPARISON_SPEC)
sys.modules[COMPARISON_SPEC.name] = COMPARISON
COMPARISON_SPEC.loader.exec_module(COMPARISON)


def private_destination(root, destination):
    root = Path(root).absolute()
    requested = Path(destination).absolute()
    content.require(root in requested.parents, "Private output must stay in selected root")
    relative = requested.relative_to(root)
    root = root.resolve()
    destination = root / relative
    expected = root / "Audiobook/author-audit/web-release"
    content.require(destination == expected, "Web audio must use the private author-audit/web-release directory")
    cursor = root
    for part in destination.relative_to(root).parts:
        cursor /= part
        content.require(not cursor.is_symlink(), "Private destination contains a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    content.require(destination.resolve() == expected, "Private destination escapes selected root")
    return destination


@contextmanager
def private_directory(destination, parts=()):
    """Open private descendants from the project descriptor, refusing symlinks."""
    destination = Path(destination).absolute()
    content.require(destination.parts[-3:] == ("Audiobook", "author-audit", "web-release"), "Invalid private output root")
    content.require(all(isinstance(part, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", part) for part in parts),
                    "Unsafe private output directory component")
    root = destination.parents[2]
    descriptors = []
    try:
        descriptors.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in ("Audiobook", "author-audit", "web-release", *parts):
            try:
                os.mkdir(part, dir_fd=descriptors[-1])
            except FileExistsError:
                pass
            descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=descriptors[-1]))
        yield descriptors[-1]
    except OSError as exc:
        raise content.ContentError("Unsafe private output symlink/path: " + str(exc)) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def private_json(destination, relative, value, *, immutable=True):
    relative = Path(relative)
    content.require(not relative.is_absolute() and bool(relative.parts)
                    and re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.json", relative.name), "Unsafe private JSON path")
    payload = content.json_bytes(value)
    temporary = ".private-" + uuid.uuid4().hex
    with private_directory(destination, relative.parts[:-1]) as descriptor:
        existing = None
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(fd, "rb") as stream:
                existing = stream.read()
        except FileNotFoundError:
            pass
        if immutable and existing is not None:
            content.require(existing == payload, "Existing immutable private JSON differs")
            return
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(temporary, relative.name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
                except FileExistsError:
                    fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
                    with os.fdopen(fd, "rb") as stream:
                        content.require(stream.read() == payload, "Concurrent immutable private JSON differs")
            else:
                os.replace(temporary, relative.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass


def private_export(destination, parts, track_id, kind, value):
    content.require(re.fullmatch(r"chapter-[0-9]{3}", track_id) and re.fullmatch(r"[a-z][a-z0-9-]*", kind),
                    "Unsafe private export identity")
    data = content.json_bytes(value)
    digest = hashlib.sha256(data).hexdigest()
    relative = Path(*parts, "tracks", track_id, kind + "." + digest + ".json")
    private_json(destination, relative, value)
    return {"path": relative.as_posix(), "sha256": digest, "bytes": len(data)}


def write_pointer(destination, name, value):
    """Replace a private convenience index without following file symlinks."""
    content.require(re.fullmatch(r"[a-z][a-z0-9-]*\.json", name) is not None, "Unsafe private pointer name")
    private_json(destination, name, value, immutable=False)


def author_approval(destination, registry, recordings, *, initialize=False):
    """Record this session's decision once; changed sources need a new review."""
    bindings = [{key: row[key] for key in ["trackId", "narratorId", "sha256", "textSha256", "selectionSha256"]}
                for row in recordings]
    with private_directory(destination):
        pass
    path = Path(destination) / "author-approval.json"
    if path.exists() or path.is_symlink():
        content.require(not path.is_symlink(), "Author approval pointer is a symlink")
        approval = content.read_json(path)
        content.require(approval.get("registrySha256") == registry["registrySha256"]
                        and approval.get("recordings") == bindings, "Author audio approval is stale for the selected release")
        return approval
    content.require(initialize, "Missing source-bound author approval; this CLI cannot invent it")
    captured = datetime.now(timezone.utc).isoformat()
    approval = {"schemaVersion": 1, "reviewer": "Rob Xie", "reviewedAt": captured, "recordedAt": captured,
                "decisionDate": "2026-10-10", "timestampScope": "Time of capturing the explicit chat decision, not a claimed message timestamp",
                "statements": [AUTHOR_APPROVAL, AUTHOR_NO_RETAKES, "ok let's just use 192 kbps files"],
                "registrySha256": registry["registrySha256"], "recordings": bindings,
                "scope": "Existing 192 kbps production audio accepted as-is and unchanged. Human alignment review remains pending."}
    private_export(destination, (), "chapter-000", "author-content-approval", approval)
    private_json(destination, "author-approval.json", approval)
    return approval


@contextmanager
def gpu_worker_lock(destination):
    with private_directory(destination) as directory:
        fd = os.open("alignment-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise content.ContentError("A local GPU alignment worker is already running") from exc
        yield
    finally:
        os.close(fd)


def token_spans(text):
    # Reuse the tracked local comparison's conservative number/initialism/brand
    # representation rules. The faithful display manuscript never changes.
    return [(token.value, token.start, token.end) for token in COMPARISON.tokens(text)]


def raw_to_master(qa, item_id, seconds):
    assembly, checks = qa.get("assembly", {}), qa.get("coordinator_checks", {})
    content.require(assembly.get("internal_silence_removed") is False, "Internal time edits require a different clock map")
    fields = ["source_samples", "leading_source_samples_trimmed", "trailing_source_samples_trimmed",
              "body_samples", "added_head_samples", "added_tail_samples"]
    content.require(all(type(assembly.get(key)) is int and assembly[key] >= 0 for key in fields), "Invalid assembly sample counts")
    rate = checks.get("sample_rate")
    content.require(type(rate) is int and rate > 0, "Missing assembly sample rate")
    total, lead, trail = (assembly[key] for key in fields[:3])
    content.require(total == lead + assembly["body_samples"] + trail and
                    checks.get("expected_master_samples") == assembly["body_samples"] + assembly["added_head_samples"] + assembly["added_tail_samples"],
                    "Assembly sample conservation differs")
    timeline = checks.get("chunk_timeline")
    content.require(isinstance(timeline, list) and bool(timeline), "Missing ordered raw chunk timeline")
    source_cursor = 0.0
    selected = None
    seen = set()
    for entry in timeline:
        start, end = entry.get("source_start_seconds"), entry.get("source_end_seconds")
        content.require(entry.get("id") not in seen and all(content.finite_number(value) for value in [start, end])
                        and abs(start - source_cursor) < 1 / rate and end > start, "Invalid raw chunk sequence")
        seen.add(entry["id"])
        expected_start = (max(start * rate, lead) - lead + assembly["added_head_samples"]) / rate
        expected_end = (min(end * rate, total - trail) - lead + assembly["added_head_samples"]) / rate
        content.require(abs(entry.get("master_start_seconds", -1) - expected_start) < 1 / rate and
                        abs(entry.get("master_end_seconds", -1) - expected_end) < 1 / rate and
                        abs(entry.get("retained_samples", -1) - (expected_end - expected_start) * rate) <= 1,
                        "Raw/master chunk timeline differs from assembly")
        if entry["id"] == item_id:
            selected = entry
        source_cursor = end
    content.require(abs(source_cursor * rate - total) <= 1 and selected is not None, "Raw timeline is incomplete or selected take missing")
    content.require(content.finite_number(seconds) and 0 <= seconds <= selected["source_end_seconds"] - selected["source_start_seconds"] + .001,
                    "Raw timestamp exceeds selected take")
    return selected["source_start_seconds"] + seconds - lead / rate + assembly["added_head_samples"] / rate


def cache_words(record, item, clip, qa):
    identity = record.get("cache_identity", {})
    text = item.get("inputtext", item.get("text"))
    content.require(record.get("id") == item["id"] == clip["id"] and record.get("expected_inputtext") == text
                    and identity.get("audio_sha256") == clip["rawAudioSha256"]
                    and identity.get("request_sha256") == item["request_sha256"]
                    and identity.get("inputtext_sha256") == hashlib.sha256(text.encode()).hexdigest(),
                    "Local ASR cache differs from the exact selected source/request/text")
    words = []
    for segment in record.get("segments", []):
        for word in segment.get("words", []):
            start, end = word.get("start"), word.get("end")
            content.require(isinstance(word.get("word"), str) and content.finite_number(start)
                            and content.finite_number(end) and 0 <= start <= end <= record["duration_seconds"] + .001,
                            f"Invalid raw ASR word timestamp for {item['id']}: {start}, {end}, duration {record['duration_seconds']}")
            # Zero-length ASR words carry no measured interval and cannot anchor a cue.
            if end > start:
                words.append((word["word"], raw_to_master(qa, item["id"], start), raw_to_master(qa, item["id"], end)))
    content.require(bool(words), "Local ASR has no measured word intervals")
    return words


def _lcs_prefix_rows(expected, heard):
    """Compact exact-token LCS prefix lengths, one bit per observed token."""
    masks = {}
    for index, token in enumerate(heard):
        masks[token] = masks.get(token, 0) | (1 << index)
    rows = [0]
    for token in expected:
        previous = rows[-1]
        union = previous | masks.get(token, 0)
        rows.append(union & ~(union - ((previous << 1) | 1)))
    return rows


def ambiguous_boundary_anchors(expected, heard, offsets, matched):
    """Reject boundary occurrences with equally supported monotone matches.

    SequenceMatcher chooses one repeated block arbitrarily. An exact-token
    pair is supported by the surrounding measured context only when its
    prefix + pair + suffix can participate in a longest common subsequence.
    Check both directions: one canonical boundary may fit several observed
    occurrences, or one observed interval may fit several canonical tokens.
    The compact rows stay scoped to this track, not the full manuscript.
    """
    if expected == heard:
        return {}
    expected_positions, heard_positions = {}, {}
    for index, token in enumerate(expected):
        expected_positions.setdefault(token, []).append(index)
    for index, token in enumerate(heard):
        heard_positions.setdefault(token, []).append(index)
    boundaries = {index for first, last in offsets for index in (first, last - 1) if first < last}
    repeated = {expected[index] for index in boundaries
                if len(expected_positions[expected[index]]) > 1 or len(heard_positions.get(expected[index], [])) > 1}
    if not repeated:
        return {}
    forward = _lcs_prefix_rows(expected, heard)
    reverse = _lcs_prefix_rows(expected[::-1], heard[::-1])
    # Python 3.9 remains supported by the clean-checkout preparation tool.
    bit_count = getattr(int, "bit_count", lambda value: bin(value).count("1"))
    length = bit_count(forward[-1])
    observed_count = len(heard)
    candidates, owners = {}, {}
    for token in repeated:
        for left in expected_positions[token]:
            options = []
            for right in heard_positions.get(token, []):
                prefix = bit_count(forward[left] & ((1 << right) - 1))
                suffix = bit_count(reverse[len(expected) - left - 1] & ((1 << (observed_count - right - 1)) - 1))
                if prefix + 1 + suffix == length:
                    options.append(right)
                    owners.setdefault(right, []).append(left)
            candidates[left] = options
    result = {}
    for index in sorted(boundaries):
        if expected[index] not in repeated:
            continue
        options = candidates[index]
        shared = [right for right in options if len(owners[right]) > 1]
        selected = matched.get(index)
        if len(options) > 1 or shared or (selected is not None and selected not in options):
            result[index] = {"expectedTokenIndex": index, "observedTokenIndexes": options,
                             "sharedObservedTokenIndexes": shared, "selectedObservedTokenIndex": selected}
    return result


def align_sentences(track, recording, words):
    previous_start = -1.0
    heard, times, pieces, spans, cursor = [], [], [], [], 0
    for word, start, end in words:
        content.require(isinstance(word, str) and content.finite_number(start) and content.finite_number(end)
                        and max(0, previous_start) <= start < end <= recording["decodedDuration"],
                        "Measured word clock is unordered or outside recording")
        previous_start = start
        pieces.append(word + " ")
        spans.append((cursor, cursor + len(word), start, end))
        cursor += len(word) + 1
    starts = [span[0] for span in spans]
    for token, first, last in token_spans("".join(pieces)):
        left = max(0, bisect_right(starts, first) - 1)
        right = max(left, bisect_left(starts, last) - 1)
        heard.append(token)
        times.append((spans[left][2], spans[right][3]))
    sentences = content.sentences(track)
    expected, offsets = [], []
    for sentence in sentences:
        first = len(expected)
        expected.extend(token for token, _, _ in token_spans(content.spoken_comparison(sentence["text"])))
        offsets.append((first, len(expected)))
    matched, substitutions = {}, []
    opcodes = difflib.SequenceMatcher(None, expected, heard, autojunk=False).get_opcodes()
    for tag, a, b, c, d in opcodes:
        if tag == "equal":
            matched.update((a + offset, c + offset) for offset in range(b - a))
    exact_matches = len(matched)
    # A minor ASR spelling between two exact words on either side has an
    # observed speech interval. Keep that confidence distinction explicit.
    for tag, a, b, c, d in opcodes:
        if tag != "replace" or b - a != 1 or d - c != 1:
            continue
        surrounding = [(a - 2, c - 2), (a - 1, c - 1), (b, d), (b + 1, d + 1)]
        if (all(left >= 0 and right >= 0 and matched.get(left) == right for left, right in surrounding)
                and difflib.SequenceMatcher(None, expected[a], heard[c]).ratio() >= .72):
            matched[a] = c
            substitutions.append({"expected": expected[a], "heard": heard[c], "expectedTokenIndex": a,
                                  "start": times[c][0], "end": times[c][1],
                                  "scope": "Measured ASR interval with adjacent exact context; spelling agreement is approximate"})
    # Context-supported spelling substitutions retain their observed intervals;
    # use the same supported token equivalence in the occurrence check only.
    # This never changes the ASR transcript, exact-match count, or word clock.
    supported_heard = list(heard)
    for substitution in substitutions:
        index = substitution["expectedTokenIndex"]
        supported_heard[matched[index]] = expected[index]
    ambiguous = ambiguous_boundary_anchors(expected, supported_heard, offsets, matched)
    cues, missing, missing_intervals, overlap, low, edge_gaps, ambiguous_sentences = [], [], [], [], [], [], []
    previous_end = 0.0
    for sentence, (first, last) in zip(sentences, offsets):
        uncertain_edges = [dict(ambiguous[index], edge=edge)
                           for edge, index in [("start", first), ("end", last - 1)] if index in ambiguous]
        if uncertain_edges:
            ambiguous_sentences.append({"sentenceId": sentence["id"], "edges": uncertain_edges,
                "scope": "Canonical occurrence is not uniquely established by measured monotone word context; no cue inferred"})
        anchors = [matched[index] for index in range(first, last) if index in matched]
        if not anchors:
            missing.append(sentence["id"])
            before = [index for index in matched if index < first]
            after = [index for index in matched if index >= last]
            previous_end_time = times[matched[max(before)]][1] if before else 0.0
            next_start_time = times[matched[min(after)]][0] if after else recording["decodedDuration"]
            missing_intervals.append({"sentenceId": sentence["id"],
                # These observed endpoints define only an ASR recheck region.
                # A recognizer can overlap neighboring speech intervals; keep
                # that uncertainty explicit without creating a sentence cue.
                "start": min(previous_end_time, next_start_time),
                "end": max(previous_end_time, next_start_time),
                "neighboringMeasuredIntervals": {"previousEnd": previous_end_time, "nextStart": next_start_time},
                "reason": "overlapping-measured-neighbors" if previous_end_time > next_start_time else "unanchored-between-measured-neighbors",
                "scope": "Neighbor-bounded recheck window, not an invented sentence cue"})
            continue
        start, end = times[anchors[0]][0], times[anchors[-1]][1]
        if start < previous_end:
            overlap.append(sentence["id"])
        # An uncertain observed interval still contributes to the conservative
        # overlap watermark; dropping it must not promote a later overlapping cue.
        previous_end = max(previous_end, end)
        fraction = len(anchors) / max(1, last - first)
        missing_edges = [edge for edge, index in [("start", first), ("end", last - 1)] if index not in matched]
        if missing_edges:
            edge_gaps.append({"sentenceId": sentence["id"], "missingEdges": missing_edges})
        # Interior agreement cannot locate omitted sentence boundaries. Exact
        # normalized tokens and the contextual substitutions above are the only
        # supported measured edge anchors; never infer their missing seconds.
        if fraction < .8 or missing_edges or uncertain_edges:
            low.append({"sentenceId": sentence["id"], "matchedFraction": round(fraction, 4)})
            continue
        cues.append({"sentenceId": sentence["id"], "start": round(start, 6), "end": round(end, 6)})
    report = {"method": "independent-local-asr-matched-word-intervals", "expectedTokens": len(expected),
              "matchedTokens": exact_matches, "matchedFraction": round(exact_matches / max(1, len(expected)), 6),
              "anchoredTokens": len(matched), "measuredSentences": cues,
              "audioSha256": recording["sha256"], "textSha256": track["textSha256"],
              "decodedDuration": recording["decodedDuration"],
              "unanchoredSentences": missing, "overlappingSentences": overlap, "lowConfidenceSentences": low,
              "unanchoredSentenceEdges": edge_gaps,
              "ambiguousSentenceAnchors": ambiguous_sentences,
              "unanchoredIntervals": missing_intervals, "contextualSubstitutions": substitutions,
              "humanAlignmentApproval": "pending", "sentenceCount": len(sentences)}
    if missing or overlap or low:
        return None, report
    timing = {"schemaVersion": 1, "trackId": track["id"], "manuscriptVersion": track["manuscriptVersion"],
              "textSha256": track["textSha256"], "narratorId": recording["narratorId"], "audioSha256": recording["sha256"],
              "duration": recording["decodedDuration"], "sentences": cues,
              "coverage": {"introEnd": cues[0]["start"], "tailStart": cues[-1]["end"]}, "approvals": {}}
    content.validate_timing_map(track, recording, timing)
    return timing, report


def refinement_windows(qa, intervals, *, context=2.0):
    """Derive bounded raw recheck windows from observed neighboring speech."""
    result = {}
    for entry in qa["coordinator_checks"]["chunk_timeline"]:
        offset = raw_to_master(qa, entry["id"], 0)
        duration = entry["source_end_seconds"] - entry["source_start_seconds"]
        windows = []
        for interval in intervals:
            start, end = interval.get("start"), interval.get("end")
            content.require(content.finite_number(start) and content.finite_number(end) and 0 <= start <= end,
                            "Invalid neighbor-bounded refinement interval")
            start = max(0.0, start - context - offset)
            end = min(duration, end + context - offset)
            if start < end:
                windows.append((start, end))
        merged = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        if merged:
            result[entry["id"]] = [(round(start, 6), round(end, 6)) for start, end in merged]
    return result


def merge_refinement_words(original, refined, windows):
    def inside(word):
        middle = (word["start"] + word["end"]) / 2
        return any(start <= middle <= end for start, end in windows)
    result = [dict(word) for word in original if not inside(word)]
    result.extend(dict(word) for word in refined if inside(word))
    return sorted(result, key=lambda word: (word["start"], word["end"]))


def measure_audio(path):
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(path)]))
    streams = probe.get("streams", [])
    content.require(len(streams) == 1 and streams[0].get("channels") == 1, "Web audio must contain one mono stream")
    stream = streams[0]
    stats = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-af",
        "astats=metadata=0:reset=0:measure_perchannel=none:measure_overall=RMS_level+Peak_level+Number_of_samples",
        "-f", "null", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stderr.decode()
    def measured(label):
        found = re.findall(re.escape(label) + r":\s*(-?\d+(?:\.\d+)?)", stats)
        content.require(bool(found), "Missing decoded audio statistic: " + label)
        return float(found[-1])
    samples, rate = int(measured("Number of samples")), int(stream["sample_rate"])
    return {"codec": stream["codec_name"], "sampleRate": rate, "channels": stream["channels"],
            "bitRate": int(stream.get("bit_rate", 0)), "decodedSamples": samples,
            "decodedDuration": samples / rate, "rmsDbfs": measured("RMS level dB"), "samplePeakDbfs": measured("Peak level dB")}


def select_recording(root, recording):
    """Retain original mastered MP3 bytes and their verified decoded clock."""
    path = content.project_file(root, recording["sourcePath"])
    content.require(content.file_hash(path) == recording["sha256"] and path.stat().st_size == recording["bytes"],
                    "Approved production MP3 bytes changed")
    qa = content.read_project_json(root, path.with_suffix(".qa.json"))
    measured = qa["decoded_mp3_qa"]
    rate = qa["coordinator_checks"]["sample_rate"]
    content.require(measured["samples"] == recording["decodedSamples"] and rate == recording["sampleRate"]
                    and abs(measured["samples"] / rate - recording["decodedDuration"]) < .000001,
                    "Approved production decoded clock differs")
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(path)]))
    streams = probe.get("streams", [])
    content.require(len(streams) == 1 and streams[0].get("codec_name") == "mp3"
                    and int(streams[0].get("bit_rate", 0)) == 192000 and streams[0].get("channels") == 1
                    and int(streams[0].get("sample_rate", 0)) == rate,
                    "Selected web audio must preserve the existing 192 kbps mono master")
    return dict(recording, audioHashVerified=True, audioVerificationSha256=recording["sha256"],
                contentApproval="approved", sourceAudioSha256=recording["sha256"],
                sourceAudioPath=recording["sourcePath"], bitRate=192000,
                quality={"rmsDbfs": measured["rms_dbfs"], "samplePeakDbfs": measured["sample_peak_dbfs"],
                         "decodedSamples": measured["samples"], "sampleRate": rate,
                         "scope": "Existing hash-bound production QA; no audio encoding or regeneration"})


def find_cache(root, destination, recording, item):
    candidates = []
    with private_directory(destination, ("alignment-cache", recording["productionEdition"], "cache")):
        pass
    for directory in [destination / "alignment-cache" / recording["productionEdition"] / "cache",
                      root / "Audiobook" / recording["productionEdition"] / "local-checks/cache"]:
        if not directory.exists():
            continue
        for path in directory.glob(item["id"] + ".*.json"):
            record = content.read_project_json(root, path)
            identity = record.get("cache_identity", {})
            clip = next(row for row in recording["selectedClips"] if row["id"] == item["id"])
            text = item.get("inputtext", item.get("text"))
            if record.get("id") == item["id"] and identity.get("request_sha256") == item["request_sha256"] and identity.get("audio_sha256") == clip["rawAudioSha256"] and record.get("expected_inputtext") == text:
                candidates.append((path, record))
    return sorted(candidates, key=lambda row: row[1].get("created_at_unix", 0), reverse=True)[0] if candidates else None


def transcribe_missing(root, destination, registry, inventory, model):
    model = Path(model).resolve()
    content.require(model == (root / "Audiobook/v6/asr-model").resolve() or model == (root / "Audiobook/v6/asr-model-medium").resolve(),
                    "Alignment accepts only an existing project-local offline model")
    # Reuse the tracked checker loaded beside this tool. Private production
    # editions and a selected --root never supply executable checker code.
    checker = COMPARISON
    identity = checker.model_identity(model)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    # Import once before any worker action. Model IDs are never accepted.
    import mlx_whisper  # noqa: F401
    for recording in inventory["recordings"]:
        manifest = content.read_project_json(root, root / "Audiobook" / recording["productionEdition"] / "generation-manifest.json")
        selected = {item["id"]: item for item in manifest["items"]}
        with private_directory(destination, ("alignment-cache", recording["productionEdition"], "cache")):
            pass
        for clip in recording["selectedClips"]:
            item = selected[clip["id"]]
            if find_cache(root, destination, recording, item):
                continue
            # Historical output paths are evidence; derive the current selected-root path.
            item = dict(item, output=str(content.project_file(root, clip["sourcePath"])))
            prepared = checker.prepare(item, identity)
            content.require(prepared[2].get("audio_sha256") == clip["rawAudioSha256"],
                            "Prepared raw audio differs from selected master source")
            started = time.monotonic()
            wav, key, cache_identity, duration = prepared
            result = mlx_whisper.transcribe(str(wav), path_or_hf_repo=identity["path"], **cache_identity["decode_settings"])
            result, warnings = checker.sanitize_backend_confidence(result)
            content.require(checker.stable_hash(wav) == clip["rawAudioSha256"], "Raw audio changed during local alignment")
            record = {"id": item["id"], "chapter": item.get("chapter"), "chunk": item.get("chunk"),
                      "cache_key": key, "cache_identity": cache_identity, "wav": str(wav), "duration_seconds": duration,
                      "expected_inputtext": checker.input_text(item), "transcript": str(result.get("text", "")),
                      "segments": result.get("segments", []), "language": result.get("language"),
                      "created_at_unix": time.time(), "backend_nonfinite_confidence_fields": warnings}
            relative = Path("alignment-cache", recording["productionEdition"], "cache", item["id"] + "." + key + ".json")
            private_json(destination, relative, record)
            print(json.dumps({"event": "local_alignment", "edition": recording["productionEdition"],
                              "id": item["id"], "status": "checked", "seconds": round(time.monotonic() - started, 2)}), flush=True)


def refine_alignment_gaps(root, destination, inventory, reports, model):
    """Recover missing ASR windows locally; narration and prose stay unchanged."""
    model = Path(model).resolve()
    content.require(model == (root / "Audiobook/v6/asr-model-medium").resolve(),
                    "Gap refinement requires the existing project-local medium model")
    identity = COMPARISON.model_identity(model)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import mlx_whisper
    by_recording = {(row["trackId"], row["narratorId"]): row for row in inventory["recordings"]}
    for report in reports:
        if not report.get("unanchoredIntervals"):
            continue
        recording = by_recording[(report["trackId"], report["narratorId"])]
        qa = content.read_project_json(root, Path(recording["sourcePath"]).with_suffix(".qa.json"))
        windows = refinement_windows(qa, report["unanchoredIntervals"])
        manifest = content.read_project_json(root, root / "Audiobook" / recording["productionEdition"] / "generation-manifest.json")
        selected = {item["id"]: item for item in manifest["items"]}
        for clip in recording["selectedClips"]:
            if clip["id"] not in windows:
                continue
            item = selected[clip["id"]]
            cached = find_cache(root, destination, recording, item)
            content.require(cached is not None, "Cannot refine a missing base timestamp cache")
            cache_path, base = cached
            attempts = base.get("refinementAttempts", [])
            needed = [(start, end) for start, end in windows[clip["id"]]
                      if not any(attempt.get("modelSha256") == identity["files_sha256"]
                                 and attempt["start"] <= start and attempt["end"] >= end for attempt in attempts)]
            if not needed:
                continue
            raw_path = content.project_file(root, clip["sourcePath"])
            content.require(content.file_hash(raw_path) == clip["rawAudioSha256"], "Refinement raw audio hash changed")
            settings = {"language": "en", "temperature": 0.0, "condition_on_previous_text": False,
                        "verbose": None, "word_timestamps": True, "initial_prompt": COMPARISON.VOCABULARY}
            started = time.monotonic()
            refined, warnings, calls, accepted, rejected, outcomes = [], [], [], [], [], []
            for start, end in needed:
                # The installed decoder does not reset seek between several
                # clip pairs; one call per window keeps rechecks bounded.
                call = dict(settings, clip_timestamps=f"{start},{end}")
                result = mlx_whisper.transcribe(str(raw_path), path_or_hf_repo=str(model), **call)
                calls.append(call)
                outcome = {"start": start, "end": end, "modelSha256": identity["files_sha256"]}
                try:
                    result, call_warnings = COMPARISON.sanitize_backend_confidence(result)
                    candidate = dict(base, segments=result.get("segments", []))
                    # Match persisted JSON before strict native-number checks:
                    # MLX can return JSON-compatible float subclasses.
                    candidate = json.loads(content.json_bytes(candidate))
                    cache_words(candidate, item, clip, qa)
                except (content.ContentError, ValueError, TypeError) as exc:
                    rejected.append({"start": start, "end": end, "reason": "invalid-raw-ASR-word-clock",
                                     "detail": str(exc)[:500]})
                    outcomes.append(dict(outcome, status="rejected-invalid-clock"))
                    continue
                refined.extend(word for segment in candidate["segments"] for word in segment.get("words", []))
                warnings.extend(call_warnings)
                accepted.append((start, end))
                outcomes.append(dict(outcome, status="measured"))
            content.require(content.file_hash(raw_path) == clip["rawAudioSha256"], "Raw audio changed during gap refinement")
            original = [word for segment in base.get("segments", []) for word in segment.get("words", [])]
            words = merge_refinement_words(original, refined, accepted)
            attempts = attempts + outcomes
            lineage = {"baseCacheSha256": content.file_hash(cache_path), "modelIdentity": identity,
                       "decodeCalls": calls, "windows": needed, "rejectedWindows": rejected, "version": VERSION}
            cache_identity = dict(base["cache_identity"], refinement=lineage)
            key = content.object_hash(cache_identity)
            record = dict(base, cache_identity=cache_identity, cache_key=key, refinementAttempts=attempts,
                          refinementFailures=base.get("refinementFailures", []) + rejected,
                          segments=[{"words": words, "text": "".join(word["word"] for word in words)}],
                          transcript="".join(word["word"] for word in words), created_at_unix=time.time(),
                          backend_nonfinite_confidence_fields=warnings)
            cache_words(record, item, clip, qa)
            # Reuse containment-safe immutable JSON export, then link it into
            # the compatible local cache namespace without overwriting a cache.
            parts = ("alignment-cache", recording["productionEdition"], "cache")
            private_export(destination, parts, recording["trackId"], "refined-asr", record)
            private_json(destination, Path(*parts, clip["id"] + "." + key + ".json"), record)
            print(json.dumps({"event": "refined_local_alignment", "edition": recording["productionEdition"],
                "id": clip["id"], "windows": needed, "seconds": round(time.monotonic() - started, 2)}), flush=True)


def build_timings(root, destination, registry, recordings, approval):
    tracks = {track["id"]: track for track in registry["tracks"]}
    maps, reports = {}, []
    for recording in recordings:
        edition, track_id = recording["productionEdition"], recording["trackId"]
        manifest = content.read_project_json(root, root / "Audiobook" / edition / "generation-manifest.json")
        selected = {item["id"]: item for item in manifest["items"]}
        qa = content.read_project_json(root, Path(recording["sourceAudioPath"]).with_suffix(".qa.json"))
        words, evidence, missing = [], [], []
        for clip in recording["selectedClips"]:
            cached = find_cache(root, destination, recording, selected[clip["id"]])
            if not cached:
                missing.append(clip["id"])
                continue
            path, record = cached
            words.extend(cache_words(record, selected[clip["id"]], clip, qa))
            cache_identity = record.get("cache_identity", {})
            evidence.append({"id": clip["id"], "cacheSha256": content.file_hash(path), "sourcePath": str(path.relative_to(root)),
                "rawAudioSha256": clip["rawAudioSha256"], "requestSha256": selected[clip["id"]]["request_sha256"],
                "expectedTextSha256": cache_identity.get("inputtext_sha256"),
                "decoderIdentitySha256": content.object_hash(cache_identity),
                "refinementAttempts": record.get("refinementAttempts", []),
                "refinementFailures": record.get("refinementFailures", [])})
        if missing:
            report = {"trackId": track_id, "narratorId": recording["narratorId"], "missingTimestampChunks": missing, "status": "pending"}
        else:
            timing, report = align_sentences(tracks[track_id], recording, words)
            report.update(trackId=track_id, narratorId=recording["narratorId"], evidence=evidence,
                          status="measured" if timing else "pending")
            report["sourceBindings"] = {"selectionSha256": recording.get("selectionSha256"),
                "masterIdentitySha256": recording.get("masterIdentitySha256"),
                "generationManifestSha256": recording.get("generationManifestSha256"),
                "sourceQaSha256": content.project_file_hash(root, Path(recording["sourceAudioPath"]).with_suffix(".qa.json"))}
            gap_windows = {gap["sentenceId"]: gap for gap in report["unanchoredIntervals"]}
            low_ids = {row["sentenceId"] for row in report["lowConfidenceSentences"]}
            report["syncGaps"] = [{"sentenceId": sentence_id, "syncStatus": "unavailable", "start": None, "end": None,
                "reason": "no-positive-duration-ASR-anchor", "recheckWindow": gap_windows[sentence_id],
                "evidenceIds": list(refinement_windows(qa, [gap_windows[sentence_id]]))}
                for sentence_id in report["unanchoredSentences"]]
            report["syncGaps"].extend({"sentenceId": sentence_id, "syncStatus": "unavailable", "start": None, "end": None,
                "reason": "overlapping-ASR-interval", "evidenceIds": [entry["id"] for entry in evidence]}
                for sentence_id in report["overlappingSentences"] if sentence_id not in low_ids)
            report["syncGaps"].extend({"sentenceId": row["sentenceId"], "syncStatus": "unavailable", "start": None, "end": None,
                "reason": "low-confidence-ASR-anchor", "evidenceIds": [entry["id"] for entry in evidence]}
                for row in report["lowConfidenceSentences"])
            if timing:
                timing["approvals"]["content"] = {"status": "approved", "reviewer": approval["reviewer"], "reviewedAt": approval["reviewedAt"],
                   "audioSha256": recording["sha256"], "textSha256": tracks[track_id]["textSha256"],
                   "sourceAudioSha256": recording["sourceAudioSha256"], "note": AUTHOR_APPROVAL + ". " + AUTHOR_NO_RETAKES}
                timing["evidence"] = evidence
                timing["sourceBindings"] = report["sourceBindings"]
                timing["alignmentMethod"] = report["method"]
                artifact = private_export(destination, ("timings",), track_id, recording["narratorId"], timing)
                report["timingPath"] = str((destination / artifact["path"]).relative_to(root))
                maps.setdefault(track_id, {})[recording["narratorId"]] = timing
        reports.append(report)
    return maps, reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--transcribe-missing", action="store_true")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--align-only", action="store_true")
    parser.add_argument("--record-author-approval", action="store_true",
                        help="Capture the already authorized 2026-10-10 audio-as-is decision once; refuses changed prior bindings")
    parser.add_argument("--refine-gaps", action="store_true", help="Locally recheck neighbor-bounded ASR gaps with the existing medium model")
    args = parser.parse_args()
    root = args.root.resolve()
    destination = private_destination(root, root / "Audiobook/author-audit/web-release")
    registry = content.load_project_registry(root)
    inventory = content.load_recording_inventory(root, registry)
    if args.transcribe_missing:
        with gpu_worker_lock(destination):
            transcribe_missing(root, destination, registry, inventory, args.model or root / "Audiobook/v6/asr-model")
        return
    if args.align_only:
        release_inventory = content.read_project_json(root, destination / "release-inventory.json")
        content.require(release_inventory["registrySha256"] == registry["registrySha256"], "Release inventory registry differs")
        recordings = release_inventory["recordings"]
        current = {(row["trackId"], row["narratorId"]): row for row in inventory["recordings"]}
        for recording in recordings:
            fresh = current[(recording["trackId"], recording["narratorId"])]
            content.require(all(recording[key] == fresh[key] for key in ["sha256", "sourcePath", "bytes", "selectionSha256", "masterIdentitySha256"]),
                            "Selected source lineage changed since release staging")
    else:
        recordings = []
        for recording in inventory["recordings"]:
            selected = select_recording(root, recording)
            recordings.append(selected)
            print(json.dumps({"event": "selected_existing_master", "trackId": selected["trackId"], "narratorId": selected["narratorId"],
                "bytes": selected["bytes"], "bitRate": selected["bitRate"], "decodedDuration": selected["decodedDuration"]}), flush=True)
        release_inventory = dict(inventory, recordings=recordings, selectionVersion=VERSION)
        private_export(destination, (), "chapter-000", "release-inventory", release_inventory)
        # Private convenience pointer; authoritative artifacts remain hash-addressed.
        write_pointer(destination, "release-inventory.json", release_inventory)
    approval = author_approval(destination, registry, recordings, initialize=args.record_author_approval)
    _, reports = build_timings(root, destination, registry, recordings, approval)
    if args.refine_gaps:
        with gpu_worker_lock(destination):
            refine_alignment_gaps(root, destination, inventory, reports, args.model or root / "Audiobook/v6/asr-model-medium")
        _, reports = build_timings(root, destination, registry, recordings, approval)
    report = {"schemaVersion": 1, "registrySha256": registry["registrySha256"], "tracks": len(registry["tracks"]),
              "storyChapters": len(registry["toc"]), "canonicalSentences": sum(len(content.sentences(track)) for track in registry["tracks"]),
              "recordings": len(recordings), "webBytes": sum(row["bytes"] for row in recordings),
              "sourceMp3Bytes": sum(row["bytes"] for row in inventory["recordings"]),
              "measuredTimings": sum(row["status"] == "measured" for row in reports), "alignmentReports": reports,
              "highestDecodedSamplePeakDbfs": max(row["quality"]["samplePeakDbfs"] for row in recordings),
              "humanAlignmentApproval": "pending"}
    private_export(destination, (), "chapter-000", "audio-release-report", report)
    write_pointer(destination, "audio-release-report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "alignmentReports"}, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except content.ContentError as exc:
        raise SystemExit("Private audio release failed: " + str(exc))
