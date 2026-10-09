#!/usr/bin/env python3
"""Resumable, local chapter mastering for LUMEN v6.

--all masters all chapters whose complete raw input set is available.
--watch processes arrivals and exits when every chapter is mastered.
--final requires all 91 chapters, masters/reuses them, and verifies the result.

Outputs are fixed at Audiobook/v6/mastered. A valid chapter checkpoint requires
matching manifest requests, raw WAV hashes, helper/settings identity and all
three output file hashes. Content QA remains a separate report: a preview master
is never labeled listening-approved or accepted by a publishing platform.
This program never reads API keys or contacts a remote service.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave

JOB = Path(__file__).resolve().parent
VERSION = "1.0.0"
EXPECTED_CHAPTERS = set(range(91))


class CoordinatorError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def object_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="." + path.name + ".",
                                         suffix=".tmp", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def chapter_title(number: int) -> str:
    if number == 0:
        return "Opening Credits and Epigraph"
    if number == 90:
        return "Closing Credits"
    return f"Chapter {number}"


def load_chapters(job: Path) -> tuple[dict, dict[int, list[dict]]]:
    try:
        manifest = json.loads((job / "generation-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CoordinatorError(f"Cannot read generation manifest: {exc}") from exc
    if manifest.get("author") != "Rob Xie":
        raise CoordinatorError("Generation manifest author must be Rob Xie")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise CoordinatorError("Generation manifest has no items")
    chapters, seen_ids = {}, set()
    raw = (job / "raw").resolve()
    for item in items:
        identity = item.get("id")
        number, chunk = item.get("chapter"), item.get("chunk")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identity) or identity in seen_ids:
            raise CoordinatorError("Duplicate or unsafe generation item id")
        if type(number) is not int or number not in EXPECTED_CHAPTERS or type(chunk) is not int or chunk < 1:
            raise CoordinatorError(f"Invalid chapter/chunk: {identity}")
        if not re.fullmatch(r"[a-f0-9]{64}", str(item.get("request_sha256", ""))):
            raise CoordinatorError(f"Invalid request hash: {identity}")
        output = Path(item.get("output", ""))
        if not output.is_absolute():
            output = job / output
        output = output.resolve()
        if output.parent != raw or output.name != identity + ".wav":
            raise CoordinatorError(f"Output must be its named WAV directly inside raw/: {identity}")
        seen_ids.add(identity)
        entry = dict(item)
        entry["resolved_output"] = output
        chapters.setdefault(number, []).append(entry)
    if set(chapters) != EXPECTED_CHAPTERS:
        raise CoordinatorError("Generation manifest must contain opening 000, chapters 001–089, and closing 090")
    for number, entries in chapters.items():
        entries.sort(key=lambda entry: entry["chunk"])
        if [entry["chunk"] for entry in entries] != list(range(1, len(entries) + 1)):
            raise CoordinatorError(f"Chapter {number:03d} chunks are not contiguous and unique")
    return manifest, chapters


def validate_inputs(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    records, problems = [], []
    for item in entries:
        wav = item["resolved_output"]
        metadata_path = wav.with_suffix(".json")
        if not wav.is_file() or not metadata_path.is_file():
            problems.append({"id": item["id"], "status": "waiting", "reason": "WAV or completed metadata unavailable"})
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            for key in ("id", "chapter", "chunk", "request_sha256", "model", "voice"):
                if metadata.get(key) != item.get(key):
                    raise CoordinatorError(f"Raw metadata {key} differs from manifest")
            audio_hash = digest(wav)
            if not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("audio_sha256", ""))):
                raise CoordinatorError("Raw metadata has no valid audio_sha256")
            if metadata["audio_sha256"] != audio_hash:
                raise CoordinatorError("Raw WAV hash differs from completed metadata")
            if metadata.get("interaction_status") not in (None, "completed"):
                raise CoordinatorError("Generation metadata is not completed")
            with wave.open(str(wav), "rb") as source:
                frames, rate = source.getnframes(), source.getframerate()
                channels, width = source.getnchannels(), source.getsampwidth()
                if not frames or rate <= 0 or channels != 1 or width != 2 or source.getcomptype() != "NONE":
                    raise CoordinatorError("Raw WAV must be nonempty mono 16-bit PCM")
                # Detect a truncated data chunk; getnframes alone trusts the header.
                read_frames = 0
                while block := source.readframes(262144):
                    if len(block) % (channels * width):
                        raise CoordinatorError("Raw WAV has an incomplete sample")
                    read_frames += len(block) // (channels * width)
                if read_frames != frames:
                    raise CoordinatorError("Raw WAV data is shorter than its header")
            if (metadata.get("sample_rate") != rate or metadata.get("channels") != channels or
                    metadata.get("sample_width") != width):
                raise CoordinatorError("Raw WAV format differs from completed metadata")
            if abs(float(metadata.get("duration_seconds", -1)) - frames / rate) > 1 / rate + 1e-9:
                raise CoordinatorError("Raw WAV duration differs from completed metadata")
            duration = frames / rate
            words = item.get("words")
            if isinstance(words, (int, float)) and words > 50 and words * 60 / duration > 250:
                raise CoordinatorError(f"Suspiciously short raw narration: {words} words in {duration:.3f} seconds exceeds 250 words/minute")
            usage = metadata.get("usage", {})
            modality_tokens = usage.get("output_tokens_by_modality", []) if isinstance(usage, dict) else []
            audio_tokens = sum(float(entry.get("tokens", 0)) for entry in modality_tokens
                               if isinstance(entry, dict) and entry.get("modality") == "audio")
            token_seconds = audio_tokens / 25
            if token_seconds > 5 and abs(duration - token_seconds) > max(1.0, token_seconds * .20):
                raise CoordinatorError(f"Raw duration {duration:.3f} seconds disagrees with reported audio-token duration {token_seconds:.3f} seconds by more than 20%")
            records.append({"id": item["id"], "chapter": item["chapter"], "chunk": item["chunk"],
                            "path": str(wav), "request_sha256": item["request_sha256"],
                            "audio_sha256": audio_hash, "native_frames": frames, "native_rate": rate,
                            "duration_seconds": frames / rate})
        except (OSError, ValueError, TypeError, wave.Error, EOFError, CoordinatorError) as exc:
            problems.append({"id": item["id"], "status": "invalid_or_stale", "reason": str(exc)})
    return records, problems


def identity_for(number: int, records: list[dict], settings: dict, helper_hash: str) -> dict:
    return {"coordinator_version": VERSION, "master_audio_sha256": helper_hash,
            "chapter": number, "title": chapter_title(number), "book": "LUMEN", "author": "Rob Xie",
            "narration": "AI-generated Gemini voice, Charon", "settings": settings,
            "inputs": records}


def destinations(output: Path, number: int) -> dict[str, Path]:
    stem = f"chapter-{number:03d}"
    return {"lossless_wav": output / (stem + ".wav"), "mp3": output / (stem + ".mp3"),
            "qa": output / (stem + ".qa.json"), "checkpoint": output / (stem + ".checkpoint.json")}


def reusable(paths: dict[str, Path], identity: dict) -> dict | None:
    if not paths["checkpoint"].is_file():
        return None
    try:
        saved = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
        if saved.get("identity") != identity or saved.get("identity_sha256") != object_digest(identity):
            return None
        for key in ("lossless_wav", "mp3", "qa"):
            if not paths[key].is_file() or digest(paths[key]) != saved["output_sha256"][key]:
                return None
        qa = json.loads(paths["qa"].read_text(encoding="utf-8"))
        if not qa.get("coordinator_checks", {}).get("duration_and_boundary_checks_passed"):
            return None
        if not qa.get("technical_ceilings_and_format_checks_passed"):
            return None
        return saved
    except (OSError, ValueError, KeyError, TypeError):
        return None


def verify_conservation(records: list[dict], qa: dict, rate: int) -> dict:
    assembly = qa["assembly"]
    sources = qa["sources"]
    if [record["path"] for record in sources] != [record["path"] for record in records]:
        raise CoordinatorError("Mastering helper changed chunk order")
    if not assembly.get("chunk_order_preserved") or assembly.get("internal_silence_removed") is not False:
        raise CoordinatorError("Mastering helper did not preserve internal audio")
    sizes = [int(source["decoded_samples"]) for source in sources]
    if any(abs(size - round(record["native_frames"] * rate / record["native_rate"])) > 2
           for size, record in zip(sizes, records)):
        raise CoordinatorError("Resampled chunk duration differs from its native WAV")
    source_count = sum(sizes)
    leading, trailing = assembly["leading_source_samples_trimmed"], assembly["trailing_source_samples_trimmed"]
    body, head, tail = assembly["body_samples"], assembly["added_head_samples"], assembly["added_tail_samples"]
    if any(type(value) is not int or value < 0 for value in (source_count, leading, trailing, body, head, tail)):
        raise CoordinatorError("Invalid sample conservation counters")
    if source_count != assembly["source_samples"] or source_count != leading + body + trailing or not body:
        raise CoordinatorError("Source/body/edge sample count conservation failed")
    expected = body + head + tail
    if qa["master_pcm_qa"]["samples"] != expected:
        raise CoordinatorError("Lossless master sample count does not preserve body and padding")
    decoded_samples = qa["decoded_mp3_qa"]["samples"]
    codec_delta = decoded_samples - expected
    codec_tolerance = max(1, round(rate * .002))
    # Some MP3 encoder/decoder padding combinations round a handful of terminal
    # samples. Keep lossless body conservation exact and bound this codec-only
    # allowance to 2 ms. Packaging uses actual decoded samples for bookmarks.
    if abs(codec_delta) > codec_tolerance:
        raise CoordinatorError(f"Decoded MP3 sample count differs by {codec_delta}; maximum codec-padding allowance is {codec_tolerance} samples (2 ms)")
    cursor, timeline = 0, []
    for record, size in zip(records, sizes):
        first, last = cursor, cursor + size
        retained_first = min(source_count - trailing, max(first, leading))
        retained_last = min(source_count - trailing, max(last, leading))
        timeline.append({"id": record["id"], "chunk": record["chunk"],
                         "source_start_seconds": first / rate, "source_end_seconds": last / rate,
                         "master_start_seconds": (max(0, retained_first - leading) + head) / rate,
                         "master_end_seconds": (max(0, retained_last - leading) + head) / rate,
                         "retained_samples": max(0, retained_last - retained_first)})
        cursor = last
    return {"duration_and_boundary_checks_passed": True, "sample_rate": rate,
            "expected_master_samples": expected, "source_seconds": source_count / rate,
            "master_seconds": expected / rate, "trimmed_outer_seconds": (leading + trailing) / rate,
            "decoded_mp3_samples": decoded_samples, "decoded_mp3_duration_seconds": decoded_samples / rate,
            "decoded_mp3_padding_delta_samples": codec_delta,
            "decoded_mp3_padding_tolerance_samples": codec_tolerance,
            "added_padding_seconds": (head + tail) / rate, "chunk_timeline": timeline,
            "body_pause_conservation": "Only outer edges are trimmed; internal chunk samples and cadence are preserved. Gain and optional peak limiting do not change timing.",
            "content_qa": "Separate local transcription and listening review; no content approval is inferred here."}


class Backend:
    def __init__(self):
        helper_path = JOB / "master_audio.py"
        self.helper_hash = digest(helper_path)
        import master_audio
        if digest(helper_path) != self.helper_hash:
            raise CoordinatorError("Mastering helper changed while loading; restart the coordinator")
        self.helper = master_audio
        self.settings = master_audio.Settings()
        self.rate = master_audio.RATE

    def master(self, records: list[dict], title: str, directory: Path) -> tuple[Path, Path, dict]:
        return self.helper.master_chapter([Path(record["path"]) for record in records], title, directory, self.settings)

    def tag(self, wav: Path, mp3: Path, title: str, number: int) -> None:
        tags = ["-metadata", f"title={title}", "-metadata", "album=LUMEN", "-metadata", "artist=Rob Xie",
                "-metadata", "album_artist=Rob Xie", "-metadata", f"track={number + 1}/91", "-metadata",
                "genre=Audiobook", "-metadata", "comment=Written by Rob Xie. Narration generated using the Gemini AI voice Charon."]
        # Stream copy rewrites tags only; encoded audio, bitrate, and PCM stay unchanged.
        before = self.helper.mp3_frames(mp3)
        for path in (wav, mp3):
            tagged = path.with_name("tagged" + path.suffix)
            command = [self.settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i",
                       str(path), "-map", "0:a:0", "-map_metadata", "-1", "-c:a", "copy"] + tags
            if path.suffix == ".mp3":
                command += ["-write_xing", "1", "-id3v2_version", "3"]
            self.helper.run(command + [str(tagged)])
            os.replace(tagged, path)
        after = self.helper.mp3_frames(mp3)
        if before != after:
            raise CoordinatorError("Metadata stream copy changed MP3 frame structure")


def make_chapter(job: Path, number: int, records: list[dict], identity: dict, backend) -> dict:
    output = job / "mastered"
    output.mkdir(parents=True, exist_ok=True)
    paths = destinations(output, number)
    cached = reusable(paths, identity)
    if cached is not None:
        return {"chapter": f"{number:03d}", "chapter_number": number, "title": chapter_title(number), "status": "reused", **cached["summary"]}
    with tempfile.TemporaryDirectory(prefix=f".chapter-{number:03d}-", dir=output) as temporary:
        work = Path(temporary)
        wav, mp3, qa = backend.master(records, chapter_title(number), work)
        if not qa.get("technical_ceilings_and_format_checks_passed"):
            raise CoordinatorError("Mastering helper did not pass technical ceiling and format checks")
        checks = verify_conservation(records, qa, backend.rate)
        backend.tag(wav, mp3, chapter_title(number), number)
        # Check actual lossless export length independently of the helper report.
        with wave.open(str(wav), "rb") as source:
            if source.getnframes() != checks["expected_master_samples"] or source.getframerate() != backend.rate or source.getnchannels() != 1 or source.getsampwidth() != 3:
                raise CoordinatorError("Actual lossless export disagrees with sample conservation or format")
        # Source replacement during mastering invalidates the result before commit.
        if any(digest(Path(record["path"])) != record["audio_sha256"] for record in records):
            raise CoordinatorError("Raw source changed during mastering; result not committed")
        qa["coordinator_checks"] = checks
        qa["book_metadata"] = {"title": "LUMEN", "author": "Rob Xie", "narration": "AI-generated Gemini voice, Charon"}
        qa["outputs"] = {key: str(paths[key]) for key in ("lossless_wav", "mp3")}
        qa["master_identity_sha256"] = object_digest(identity)
        qa_file = work / "chapter.qa.json"
        atomic_json(qa_file, qa)
        summary = {"mp3": str(paths["mp3"]), "lossless_wav": str(paths["lossless_wav"]), "qa": str(paths["qa"]),
                   "duration_seconds": checks["master_seconds"], "rms_dbfs": qa["decoded_mp3_qa"]["rms_dbfs"],
                   "sample_peak_dbfs": qa["decoded_mp3_qa"]["sample_peak_dbfs"], "warnings": qa.get("warnings", []),
                   "rms_target_met_within_0_5_db": qa.get("rms_target_met_within_0_5_db", False)}
        output_hashes = {"lossless_wav": digest(wav), "mp3": digest(mp3), "qa": digest(qa_file)}
        for key, temporary_path in (("lossless_wav", wav), ("mp3", mp3), ("qa", qa_file)):
            os.replace(temporary_path, paths[key])
        # Checkpoint last: interruption between output replacements causes safe regeneration.
        checkpoint = {"identity": identity, "identity_sha256": object_digest(identity), "output_sha256": output_hashes,
                      "summary": summary, "completed_at_unix": time.time()}
        atomic_json(paths["checkpoint"], checkpoint)
    return {"chapter": f"{number:03d}", "chapter_number": number, "title": chapter_title(number), "status": "mastered", **summary}


def content_status(job: Path, entries: list[dict], records: list[dict]) -> dict:
    cache = job / "local-checks" / "cache"
    checked, flagged = [], []
    by_id = {record["id"]: record for record in records}
    for item in entries:
        record = by_id.get(item["id"])
        if record is None:
            continue
        candidates = sorted(cache.glob(item["id"] + ".*.json"), key=lambda path: path.stat().st_mtime, reverse=True) if cache.is_dir() else []
        for path in candidates:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                match = saved.get("cache_identity", {})
                if match.get("audio_sha256") != record["audio_sha256"] or match.get("request_sha256") != record["request_sha256"]:
                    continue
                checked.append(item["id"])
                if saved.get("comparison", {}).get("needs_review", True):
                    flagged.append(item["id"])
                break
            except (OSError, ValueError, TypeError):
                continue
    missing = [item["id"] for item in entries if item["id"] not in checked]
    status = "local_asr_review_candidates" if flagged else ("awaiting_local_asr" if missing else "local_asr_no_flagged_discrepancy")
    return {"status": status, "checked_items": checked, "review_candidate_ids": flagged, "pending_ids": missing,
            "listening_approval": False, "scope": "Local ASR candidates are not confirmed narration errors or listening approval."}


def scan(job: Path, backend, workers: int = 1) -> dict:
    manifest_hash = digest(job / "generation-manifest.json")
    manifest, chapters = load_chapters(job)
    if digest(job / "generation-manifest.json") != manifest_hash:
        raise CoordinatorError("Generation manifest changed while reading; retry the coordinator")
    chapter_manifest = {f"{number:03d}": {"title": chapter_title(number),
                        "paths": [str(item["resolved_output"]) for item in chapters[number]],
                        "request_sha256": [item["request_sha256"] for item in chapters[number]]}
                        for number in range(91)}
    atomic_json(job / "chapters.json", chapter_manifest)
    helper_hash = digest(JOB / "master_audio.py")
    if getattr(backend, "helper_hash", helper_hash) != helper_hash:
        raise CoordinatorError("Mastering helper changed after loading; restart the coordinator")
    report = {"coordinator_version": VERSION, "updated_at_unix": time.time(), "book": "LUMEN", "author": "Rob Xie",
              "narration": "AI-generated Gemini voice, Charon", "chapters_expected": 91,
              "chapters": [], "pending_chapters": [], "errors": [], "complete": False,
              "content_qa_complete_without_flags": False, "publishing_acceptance": "Not assessed"}
    settings = asdict(backend.settings)
    jobs = []
    for number in range(91):
        entries = chapters[number]
        records, problems = validate_inputs(entries)
        if problems:
            report["pending_chapters"].append({"chapter": number, "items": problems})
            continue
        identity = identity_for(number, records, settings, helper_hash)
        jobs.append((number, entries, records, identity))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lumen-master")
    futures = {}
    try:
        for number, entries, records, identity in jobs:
            future = pool.submit(make_chapter, job, number, records, identity, backend)
            futures[future] = (number, entries, records)
        for future in as_completed(futures):
            number, entries, records = futures[future]
            try:
                result = future.result()
                result["content_qa"] = content_status(job, entries, records)
                report["chapters"].append(result)
                report["chapters"].sort(key=lambda chapter: chapter["chapter_number"])
                if result["status"] == "mastered":
                    print(json.dumps({"chapter": number, "status": "mastered", "duration_seconds": result["duration_seconds"],
                                      "rms_dbfs": result["rms_dbfs"], "warnings": len(result["warnings"])}), flush=True)
            except (OSError, ValueError, KeyError, TypeError, wave.Error, EOFError, CoordinatorError, RuntimeError) as exc:
                report["errors"].append({"chapter": number, "error": str(exc)})
                report["errors"].sort(key=lambda error: error["chapter"])
            # Workers only commit their own outputs/checkpoints. This main thread
            # owns the ordered aggregate report and content-QA snapshot.
            atomic_json(job / "mastered" / "mastering-report.json", report)
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        # On interruption cancel queued jobs, then let at most two active chapter
        # jobs finish their atomic checkpoint/cleanup before releasing the flock.
        pool.shutdown(wait=True, cancel_futures=True)
    report["manifest_changed_during_scan"] = digest(job / "generation-manifest.json") != manifest_hash
    report["complete"] = (len(report["chapters"]) == 91 and not report["pending_chapters"] and
                          not report["errors"] and not report["manifest_changed_during_scan"])
    report["generation_manifest_sha256"] = manifest_hash
    report["content_qa_complete_without_flags"] = report["complete"] and all(chapter["content_qa"]["status"] == "local_asr_no_flagged_discrepancy" for chapter in report["chapters"])
    report["duration_seconds"] = sum(chapter["duration_seconds"] for chapter in report["chapters"])
    report["technical_warning_chapters"] = [chapter["chapter"] for chapter in report["chapters"] if chapter["warnings"]]
    atomic_json(job / "mastered" / "mastering-report.json", report)
    return report


@dataclass
class MockSettings:
    target_rms_db: float = -20.0
    peak_ceiling_db: float = -3.2
    head_seconds: float = 1.0
    tail_seconds: float = 2.0


class MockBackend:
    rate = 24000
    def __init__(self):
        self.settings, self.calls = MockSettings(), 0
    def master(self, records, title, directory):
        self.calls += 1
        sizes = [record["native_frames"] for record in records]
        body, head, tail = sum(sizes) - 2, self.rate, 2 * self.rate
        wav, mp3 = directory / "master.wav", directory / "master.mp3"
        with wave.open(str(wav), "wb") as output:
            output.setnchannels(1); output.setsampwidth(3); output.setframerate(self.rate)
            output.writeframes(b"\0" * 3 * (body + head + tail))
        mp3.write_bytes(b"mock-mp3-no-real-mastering")
        qa = {"sources": [{"path": record["path"], "decoded_samples": size} for record, size in zip(records, sizes)],
              "assembly": {"chunk_order_preserved": True, "internal_silence_removed": False, "source_samples": sum(sizes),
                           "body_samples": body, "leading_source_samples_trimmed": 1, "trailing_source_samples_trimmed": 1,
                           "added_head_samples": head, "added_tail_samples": tail},
              "master_pcm_qa": {"samples": body + head + tail},
              "decoded_mp3_qa": {"samples": body + head + tail, "rms_dbfs": -20.1, "sample_peak_dbfs": -3.9},
              "technical_ceilings_and_format_checks_passed": True, "rms_target_met_within_0_5_db": True, "warnings": []}
        return wav, mp3, qa
    def tag(self, wav, mp3, title, number):
        pass


def selftest() -> dict:
    checks = []
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise CoordinatorError("Self-test failed: " + name)
    with tempfile.TemporaryDirectory(prefix="lumen-master-coordinator-test-") as temporary:
        job = Path(temporary)
        (job / "raw").mkdir()
        items = []
        for number in range(91):
            count = 2 if number == 1 else 1
            for chunk in range(1, count + 1):
                identity = f"chapter-{number:03d}-{chunk:03d}"
                items.append({"id": identity, "chapter": number, "chunk": chunk, "request_sha256": object_digest(identity),
                              "model": "test", "voice": "Charon", "output": str(job / "raw" / (identity + ".wav"))})
        atomic_json(job / "generation-manifest.json", {"author": "Rob Xie", "items": items})
        for item in items[:3]:
            path = Path(item["output"])
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                output.writeframes(b"\1\0" * 2400)
            atomic_json(path.with_suffix(".json"), {**{key: item[key] for key in ("id", "chapter", "chunk", "request_sha256", "model", "voice")},
                        "audio_sha256": digest(path), "duration_seconds": .1, "sample_rate": 24000, "sample_width": 2, "channels": 1,
                        "interaction_status": "completed"})
        _, chapters = load_chapters(job)
        records, problems = validate_inputs(chapters[1])
        check("contiguous ordered multi-chunk inputs validated", len(records) == 2 and not problems)
        backend = MockBackend()
        identity = identity_for(1, records, asdict(backend.settings), "helper-v1")
        first = make_chapter(job, 1, records, identity, backend)
        second = make_chapter(job, 1, records, identity, backend)
        check("matching outputs reuse checkpoint without mastering", first["status"] == "mastered" and second["status"] == "reused" and backend.calls == 1)
        paths = destinations(job / "mastered", 1)
        qa = json.loads(paths["qa"].read_text())
        timeline = qa["coordinator_checks"]["chunk_timeline"]
        check("chunk boundary timestamps preserve internal cadence", timeline[0]["master_end_seconds"] == timeline[1]["master_start_seconds"])
        check("duration equals body plus padding", qa["coordinator_checks"]["expected_master_samples"] == 4800 - 2 + 72000)
        paths["mp3"].write_bytes(b"corrupted-output")
        check("corrupt output cannot be reused", reusable(paths, identity) is None)
        make_chapter(job, 1, records, identity, backend)
        check("corrupt output safely remastered", backend.calls == 2)
        changed = json.loads(json.dumps(identity)); changed["settings"]["target_rms_db"] = -21
        check("settings change invalidates checkpoint", reusable(paths, changed) is None)
        changed = json.loads(json.dumps(identity)); changed["master_audio_sha256"] = "helper-v2"
        check("helper change invalidates checkpoint", reusable(paths, changed) is None)
        changed = json.loads(json.dumps(identity)); changed["inputs"][0]["request_sha256"] = "b" * 64
        check("request change invalidates checkpoint", reusable(paths, changed) is None)
        metadata_path = Path(records[0]["path"]).with_suffix(".json")
        metadata = json.loads(metadata_path.read_text()); original_metadata = dict(metadata)
        metadata["request_sha256"] = "a" * 64; atomic_json(metadata_path, metadata)
        _, bad = validate_inputs(chapters[1])
        check("stale raw request prevented from mastering", bool(bad))
        atomic_json(metadata_path, original_metadata)
        short_item = dict(chapters[1][0]); short_item["words"] = 700
        _, bad = validate_inputs([short_item])
        check("grossly short long narration rejected by pace", bool(bad) and "250 words/minute" in bad[0]["reason"])
        metadata = dict(original_metadata)
        metadata["usage"] = {"output_tokens_by_modality": [{"modality": "audio", "tokens": 6250}]}
        atomic_json(metadata_path, metadata)
        _, bad = validate_inputs([chapters[1][0]])
        check("partial audio rejected by reported audio-token duration", bool(bad) and "audio-token duration" in bad[0]["reason"])
        atomic_json(metadata_path, original_metadata)
        with Path(records[0]["path"]).open("ab") as output: output.write(b"corrupt")
        _, bad = validate_inputs(chapters[1])
        check("raw audio hash mismatch prevented from mastering", bool(bad))
        no_qa = content_status(job, chapters[1], records)
        check("preview masters do not infer content approval", no_qa["status"] == "awaiting_local_asr" and not no_qa["listening_approval"])
        (job / "local-checks" / "cache").mkdir(parents=True)
        for item, record in zip(chapters[1], records):
            atomic_json(job / "local-checks" / "cache" / (item["id"] + ".test.json"),
                        {"cache_identity": {"audio_sha256": record["audio_sha256"], "request_sha256": record["request_sha256"]},
                         "comparison": {"needs_review": item["chunk"] == 2}})
        with_qa = content_status(job, chapters[1], records)
        check("matching ASR review candidates remain separate", with_qa["status"] == "local_asr_review_candidates" and with_qa["review_candidate_ids"] == [chapters[1][1]["id"]])
        qa["assembly"]["internal_silence_removed"] = True
        try: verify_conservation(records, qa, backend.rate)
        except CoordinatorError: rejected = True
        else: rejected = False
        check("internal silence removal rejected", rejected)
        qa["assembly"]["internal_silence_removed"] = False
        qa["decoded_mp3_qa"]["samples"] += 11
        bounded = verify_conservation(records, qa, backend.rate)
        check("sub-millisecond codec padding rounding accepted and recorded", bounded["decoded_mp3_padding_delta_samples"] == 11)
        qa["decoded_mp3_qa"]["samples"] += round(backend.rate * .002) + 1
        try: verify_conservation(records, qa, backend.rate)
        except CoordinatorError: rejected = True
        else: rejected = False
        check("decoded duration beyond 2 ms allowance rejected", rejected)
        items[1]["output"] = str(job / "outside.wav")
        atomic_json(job / "generation-manifest.json", {"author": "Rob Xie", "items": items})
        try: load_chapters(job)
        except CoordinatorError: rejected = True
        else: rejected = False
        check("raw path escape rejected", rejected)
    report = {"passed": True, "checks": checks, "checks_count": len(checks),
              "scope": "Mock helper only; no production mastering, model, API, key or cloud access", "created_at_unix": time.time()}
    atomic_json(JOB / "master-chapters-selftest.json", report)
    print(json.dumps({"selftest_passed": True, "checks": len(checks)}), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--all", action="store_true")
    modes.add_argument("--watch", action="store_true")
    modes.add_argument("--final", action="store_true")
    modes.add_argument("--selftest", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1,
                        help="bounded chapter CPU workers; DSP/settings and cache identities are unchanged")
    args = parser.parse_args()
    if args.selftest:
        selftest(); return 0
    if args.poll_seconds < 1 or args.poll_seconds > 60:
        parser.error("--poll-seconds must be between 1 and 60")
    with (JOB / ".master-chapters.lock").open("a+") as lock:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CoordinatorError("Another chapter mastering coordinator is already running")
        backend = Backend()
        while True:
            report = scan(JOB, backend, args.workers)
            print(json.dumps({"mastered_chapters": len(report["chapters"]), "pending_chapters": len(report["pending_chapters"]),
                              "errors": len(report["errors"]), "complete": report["complete"],
                              "content_qa_complete_without_flags": report["content_qa_complete_without_flags"]}), flush=True)
            if report["errors"]:
                return 1
            if args.final and not report["complete"]:
                print("Final mastering requires all 91 complete, matching raw chapter inputs.", file=sys.stderr)
                return 1
            if not args.watch or report["complete"]:
                return 0
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CoordinatorError, OSError, ValueError, RuntimeError) as exc:
        print("Mastering coordinator: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
