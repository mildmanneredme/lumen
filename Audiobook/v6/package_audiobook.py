#!/usr/bin/env python3
"""Package complete locally mastered LUMEN audio; no APIs or uploads.

  python package_audiobook.py --input-list chapters.json --mastered-dir mastered \
      --mastering-report mastered/mastering-report.json --output-dir delivery
  python package_audiobook.py --self-test --test-report package-self-test.json

The ordered input JSON matches master_audio.py: keys 000..090, each with title
and paths. The final book requires opening 000, all 89 chapters, and closing 090.
Every mastered MP3 must be mono 44.1 kHz / 192 kbps, and its verified lossless WAV
must be mono 44.1 kHz / 24-bit PCM. A completed mastering report must list exactly
those chapters in order. The M4B encodes directly from the lossless WAVs, once.
Chapter times use exact decoded WAV samples. The ZIP preserves original MP3s;
source files are read only.

Deliverables: bookmarked M4B (AAC 128 kbps mono), original chapter MP3s in a ZIP,
README, optional <=5-minute MP3 sample, and hashes / timings / ffprobe evidence.
Reuses a verified matching delivery instead of encoding it again. A final
manifest is written last; incomplete production cannot be packaged as complete.
"""

from __future__ import annotations

import argparse
from array import array
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import wave


RATE = 44100
SAMPLE_BYTES = 4
VERSION = 2
AAC_BITRATE = 128000


class PackagingError(RuntimeError):
    pass


def canonical(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackagingError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    except (OSError, ValueError) as exc:
        raise PackagingError(f"Cannot read {path}: {exc}") from exc


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def run(command: list[str], *, stdout=None) -> bytes:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                stdout=stdout if stdout is not None else subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise PackagingError(f"Cannot run {command[0]}: {exc}") from exc
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise PackagingError(f"{Path(command[0]).name} failed: {error}")
    return result.stdout or b""


def probe(path: Path, ffprobe: str) -> dict:
    return json.loads(run([ffprobe, "-v", "error", "-show_streams", "-show_chapters",
                           "-show_format", "-of", "json", str(path)]))


def audio_stream(data: dict) -> dict:
    streams = [entry for entry in data.get("streams", []) if entry.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise PackagingError("Expected exactly one audio stream")
    return streams[0]


def clean_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise PackagingError(f"Invalid {field}; use nonempty text without control characters")
    if value.endswith("\\"):
        raise PackagingError(f"Invalid {field}; FFmetadata cannot preserve a trailing backslash safely")
    return value


def source_binding(key: str, chapter: dict, input_list: Path, mastered_dir: Path,
                   report: dict, mp3_hash: str, helper_hash: str) -> dict:
    """Bind current requests/raw audio to the exact checkpointed mastered outputs."""
    source_paths, requests = chapter.get("paths"), chapter.get("request_sha256")
    if (not isinstance(source_paths, list) or not source_paths
            or any(not isinstance(path, str) or not path for path in source_paths)
            or not isinstance(requests, list) or len(requests) != len(source_paths)
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in requests)):
        raise PackagingError(f"Chapter {key} needs ordered current paths and request_sha256 arrays")
    raw_paths = [(input_list.parent / value).resolve() for value in source_paths]
    checkpoint_path = mastered_dir / f"chapter-{key}.checkpoint.json"
    checkpoint = load_json(checkpoint_path)
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("identity"), dict):
        raise PackagingError(f"Missing valid mastering checkpoint for {key}")
    identity = checkpoint["identity"]
    identity_hash = hashlib.sha256(canonical(identity)).hexdigest()
    if checkpoint.get("identity_sha256") != identity_hash:
        raise PackagingError(f"Mastering checkpoint identity hash differs for {key}")
    if (identity.get("chapter") != int(key) or identity.get("author") != "Rob Xie"
            or identity.get("book") != "LUMEN" or identity.get("title") != chapter.get("title")
            or identity.get("master_audio_sha256") != helper_hash
            or identity.get("coordinator_version") != report.get("coordinator_version")):
        raise PackagingError(f"Mastering checkpoint settings/helper/metadata are stale for {key}")
    records = identity.get("inputs")
    if (not isinstance(records, list) or len(records) != len(raw_paths)
            or any(not isinstance(x, dict) or not isinstance(x.get("path"), str) for x in records)):
        raise PackagingError(f"Mastering checkpoint source coverage differs for {key}")
    if ([str(Path(record.get("path", "")).resolve()) for record in records] != [str(path) for path in raw_paths]
            or [record.get("request_sha256") for record in records] != requests):
        raise PackagingError(f"Mastered chapter {key} belongs to different narration requests")
    for index, (path, request, record) in enumerate(zip(raw_paths, requests, records), 1):
        metadata = load_json(path.with_suffix(".json"))
        audio_hash = digest(path)
        if (not isinstance(metadata, dict) or metadata.get("request_sha256") != request
                or metadata.get("audio_sha256") != audio_hash or record.get("audio_sha256") != audio_hash
                or metadata.get("chapter") != int(key) or metadata.get("chunk") != index
                or metadata.get("id") != record.get("id")):
            raise PackagingError(f"Raw audio or completed metadata changed since mastering {key}, chunk {index}")
    output_hashes = checkpoint.get("output_sha256", {})
    if not isinstance(output_hashes, dict) or output_hashes.get("mp3") != mp3_hash:
        raise PackagingError(f"Mastered MP3 hash differs from checkpoint for {key}")
    qa_path = mastered_dir / f"chapter-{key}.qa.json"
    wav_path = mastered_dir / f"chapter-{key}.wav"
    for label, path in (("qa", qa_path), ("lossless_wav", wav_path)):
        if not path.is_file() or digest(path) != output_hashes.get(label):
            raise PackagingError(f"Mastered {label} hash differs from checkpoint for {key}")
    qa = load_json(qa_path)
    if (not isinstance(qa, dict) or qa.get("master_identity_sha256") != identity_hash
            or qa.get("technical_ceilings_and_format_checks_passed") is not True
            or qa.get("coordinator_checks", {}).get("duration_and_boundary_checks_passed") is not True):
        raise PackagingError(f"Mastered chapter {key} does not carry matching technical and duration checks")
    return {"checkpoint_sha256": digest(checkpoint_path), "identity_sha256": identity_hash,
            "master_audio_sha256": helper_hash,
            "raw_inputs": [{"path": str(path), "request_sha256": request, "audio_sha256": record["audio_sha256"]}
                           for path, request, record in zip(raw_paths, requests, records)],
            "output_sha256": output_hashes}


def inputs(input_list: Path, mastered_dir: Path, report_path: Path, book_chapters: int,
           ffprobe: str) -> tuple[list[dict], dict]:
    content = load_json(input_list)
    mapping = content.get("chapters", content) if isinstance(content, dict) else None
    if not isinstance(mapping, dict) or not mapping:
        raise PackagingError("Input-list must contain an ordered chapters mapping")
    expected = [f"{index:03d}" for index in range(book_chapters + 2)]
    if list(mapping) != expected:
        raise PackagingError(f"Complete ordered keys required: 000..{book_chapters + 1:03d}; "
                             f"received {len(mapping)} entries")
    report = load_json(report_path)
    if not isinstance(report, dict) or report.get("complete") is not True:
        raise PackagingError("Mastering report is incomplete")
    generation_path = input_list.parent / "generation-manifest.json"
    helper_path = input_list.parent / "master_audio.py"
    if (not generation_path.is_file() or report.get("generation_manifest_sha256") != digest(generation_path)):
        raise PackagingError("Mastering report belongs to an old or missing generation manifest")
    if not helper_path.is_file():
        raise PackagingError("Current mastering helper is unavailable for checkpoint verification")
    helper_hash = digest(helper_path)
    entries = report.get("chapters")
    if (not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries)
            or [str(x.get("chapter")) for x in entries] != expected):
        raise PackagingError("Mastering report chapter count or order differs from input-list")
    records = []
    for key, report_entry in zip(expected, entries):
        chapter = mapping[key]
        if not isinstance(chapter, dict):
            raise PackagingError(f"Invalid chapter record: {key}")
        title = clean_text(chapter.get("title", f"Chapter {key}"), "chapter title")
        path = (mastered_dir / f"chapter-{key}.mp3").resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise PackagingError(f"Missing mastered chapter: {path}")
        reported_path = Path(str(report_entry.get("mp3", "")))
        if not reported_path.is_absolute():
            reported_path = report_path.parent / reported_path
        if reported_path.resolve() != path:
            raise PackagingError(f"Mastering report file mismatch for {key}")
        information = probe(path, ffprobe)
        stream = audio_stream(information)
        if (stream.get("codec_name") != "mp3" or int(stream.get("sample_rate", 0)) != RATE
                or int(stream.get("channels", 0)) != 1
                or abs(int(stream.get("bit_rate", 0)) - 192000) > 1000):
            raise PackagingError(f"Chapter {key} is not a mastered 44.1 kHz mono 192 kbps MP3")
        duration = float(information.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise PackagingError(f"Invalid duration for {key}")
        mp3_hash = digest(path)
        binding = source_binding(key, chapter, input_list, mastered_dir, report, mp3_hash, helper_hash)
        wav = path.with_suffix(".wav")
        wav_information = probe(wav, ffprobe)
        wav_stream = audio_stream(wav_information)
        if (wav_stream.get("codec_name") != "pcm_s24le" or int(wav_stream.get("sample_rate", 0)) != RATE
                or int(wav_stream.get("channels", 0)) != 1
                or int(wav_stream.get("bits_per_sample", 0)) != 24):
            raise PackagingError(f"Chapter {key} is not a mastered44.1kHz mono24-bit lossless WAV")
        with wave.open(str(wav), "rb") as source:
            declared_samples = source.getnframes()
            if (declared_samples <= 0 or source.getframerate() != RATE
                    or source.getnchannels() != 1 or source.getsampwidth() != 3):
                raise PackagingError(f"Chapter {key} has an invalid lossless WAV header")
        wav_hash = binding["output_sha256"]["lossless_wav"]
        if digest(wav) != wav_hash:
            raise PackagingError(f"Lossless WAV changed while validating chapter {key}")
        records.append({"key": key, "title": title, "file": str(path), "sha256": mp3_hash,
                        "source_binding": binding,
                        "m4b_source": {"file": str(wav), "sha256": wav_hash, "declared_pcm_samples": declared_samples,
                                       "duration_seconds": declared_samples / RATE,
                                       "codec": {name: wav_stream.get(name) for name in
                                                 ("codec_name", "sample_rate", "channels", "bits_per_sample")}},
                        "bytes": path.stat().st_size, "mp3_container_duration_seconds": duration,
                        "source_codec": {name: stream.get(name) for name in
                                         ("codec_name", "sample_rate", "channels", "bit_rate")}})
    return records, report


def metadata_escape(value: str) -> str:
    return re.sub(r"([\\=;#])", r"\\\1", value)


def build_metadata(path: Path, records: list[dict], title: str, author: str, narrator: str) -> None:
    lines = [";FFMETADATA1", f"title={metadata_escape(title)}", f"album={metadata_escape(title)}",
             f"artist={metadata_escape(author)}", f"album_artist={metadata_escape(author)}",
             f"composer={metadata_escape(author)}", "genre=Audiobook", f"narrator={metadata_escape(narrator)}",
             f"comment={metadata_escape('Written by ' + author + '. Narration: ' + narrator + '. Generated using Gemini 3.8 Flash-Lite TTS.')}"]
    for item in records:
        lines += ["[CHAPTER]", f"TIMEBASE=1/{RATE}", f"START={item['start_sample']}",
                  f"END={item['end_sample']}", f"title={metadata_escape(item['title'])}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_m4b(path: Path, records: list[dict], title: str, author: str, ffprobe: str) -> dict:
    data = probe(path, ffprobe)
    stream = audio_stream(data)
    if (stream.get("codec_name") != "aac" or int(stream.get("sample_rate", 0)) != RATE
            or int(stream.get("channels", 0)) != 1):
        raise PackagingError("M4B audio codec verification failed")
    chapters = data.get("chapters", [])
    if len(chapters) != len(records):
        raise PackagingError("M4B chapter count verification failed")
    for chapter, record in zip(chapters, records):
        if chapter.get("tags", {}).get("title") != record["title"]:
            raise PackagingError(f"M4B chapter title/order verification failed for {record['key']}")
        for name, expected in (("start_time", record["start_sample"] / RATE),
                               ("end_time", record["end_sample"] / RATE)):
            if abs(float(chapter.get(name, -1)) - expected) > .002:
                raise PackagingError(f"M4B chapter timing verification failed for {record['key']}")
    expected_duration = records[-1]["end_sample"] / RATE
    actual_duration = float(data.get("format", {}).get("duration", -1))
    if abs(actual_duration - expected_duration) > .12:
        raise PackagingError("M4B total duration differs from concatenated decoded audio")
    tags = data.get("format", {}).get("tags", {})
    if tags.get("title") != title or tags.get("artist") != author:
        raise PackagingError("M4B title/author metadata verification failed")
    if "Gemini (Charon AI voice)" not in tags.get("comment", ""):
        raise PackagingError("M4B explicit AI narrator credit is missing from metadata")
    return {"passed": True, "chapter_count": len(chapters), "expected_duration_seconds": expected_duration,
            "container_duration_seconds": actual_duration, "duration_difference_seconds": actual_duration - expected_duration,
            "chapter_timing_tolerance_seconds": .002, "ffprobe": data}


def readme_text(title: str, author: str, narrator: str, records: list[dict], sample: bool) -> str:
    duration = records[-1]["end_sample"] / RATE
    hours, remainder = divmod(round(duration), 3600)
    minutes, seconds = divmod(remainder, 60)
    return (f"{title}\nWritten by {author}\nNarration: {narrator}\n"
            "Generated using Gemini 3.8 Flash-Lite TTS.\n\n"
            f"Runtime: {hours}:{minutes:02d}:{seconds:02d}\n"
            f"Tracks: {len(records)} (opening credits, {len(records)-2} chapters, closing credits)\n\n"
            "The .m4b is one complete audiobook with chapter bookmarks (AAC, mono, 44.1 kHz, 128 kbps),\n"
            "encoded directly from the verified lossless chapter masters.\n"
            "The chapter ZIP contains the original mastered MP3s (mono, 44.1 kHz, 192 kbps).\n"
            "Import the M4B into an audiobook player, or extract and play MP3 tracks in filename order.\n"
            + ("The sample MP3 is an excerpt from Chapter 1, at most five minutes.\n" if sample else "")
            + "The delivery manifest records source and output SHA-256 hashes, exact decoded chapter\n"
              "timings, and technical container verification. No distribution listing is included.\n")


def check_cache(output_dir: Path, build_hash: str, records: list[dict], title: str, author: str,
                ffprobe: str) -> dict | None:
    receipt_path = output_dir / "delivery-manifest.json"
    if not receipt_path.is_file():
        return None
    try:
        receipt = load_json(receipt_path)
        if receipt.get("complete") is not True or receipt.get("build_sha256") != build_hash:
            return None
        outputs = receipt.get("outputs", [])
        if not outputs or any(not (output_dir / item["file"]).is_file() or
                              digest(output_dir / item["file"]) != item["sha256"] for item in outputs):
            return None
        timings = receipt.get("chapters", [])
        if [item["key"] for item in timings] != [item["key"] for item in records]:
            return None
        verify_m4b(output_dir / receipt["m4b_file"], timings, title, author, ffprobe)
        return receipt
    except (PackagingError, KeyError, TypeError, OSError, ValueError):
        return None


def frozen_inputs(records: list[dict], input_list: Path, input_hash: str, report_path: Path,
                  report_hash: str, generation_hash: str) -> None:
    if digest(input_list) != input_hash or digest(report_path) != report_hash:
        raise PackagingError("Chapter mapping or mastering report changed during packaging")
    if digest(input_list.parent / "generation-manifest.json") != generation_hash:
        raise PackagingError("Generation manifest changed during packaging")
    for item in records:
        mp3 = Path(item["file"])
        if digest(mp3) != item["sha256"]:
            raise PackagingError(f"Mastered MP3 changed during packaging: {item['key']}")
        binding = item["source_binding"]
        if digest(input_list.parent / "master_audio.py") != binding["master_audio_sha256"]:
            raise PackagingError("Mastering helper changed during packaging")
        for label, path in (("lossless_wav", mp3.with_suffix(".wav")), ("qa", mp3.with_suffix(".qa.json"))):
            if digest(path) != binding["output_sha256"][label]:
                raise PackagingError(f"Mastered {label} changed during packaging: {item['key']}")
        if digest(mp3.with_suffix(".checkpoint.json")) != binding["checkpoint_sha256"]:
            raise PackagingError(f"Mastering checkpoint changed during packaging: {item['key']}")
        for raw in item["source_binding"]["raw_inputs"]:
            path = Path(raw["path"])
            metadata = load_json(path.with_suffix(".json"))
            if (digest(path) != raw["audio_sha256"] or not isinstance(metadata, dict)
                    or metadata.get("audio_sha256") != raw["audio_sha256"]
                    or metadata.get("request_sha256") != raw["request_sha256"]):
                raise PackagingError(f"Raw narration changed during packaging: {item['key']}")


def package(input_list: Path, mastered_dir: Path, report_path: Path, output_dir: Path, *,
            book_chapters: int = 89, title: str = "LUMEN", author: str = "Rob Xie",
            narrator: str = "Gemini (Charon AI voice)", sample_seconds: float = 299.9,
            ffmpeg: str = "/opt/homebrew/bin/ffmpeg", ffprobe: str = "/opt/homebrew/bin/ffprobe") -> dict:
    title, author, narrator = [clean_text(value, field) for value, field in
                              ((title, "title"), (author, "author"), (narrator, "narrator"))]
    if book_chapters < 1 or not 0 <= sample_seconds <= 300:
        raise PackagingError("Chapter count must be positive; sample must be 0..300 seconds")
    if output_dir.resolve() == mastered_dir.resolve():
        raise PackagingError("Delivery output must be separate from mastered sources")
    input_hash, report_hash = digest(input_list), digest(report_path)
    records, report = inputs(input_list, mastered_dir, report_path, book_chapters, ffprobe)
    if digest(input_list) != input_hash or digest(report_path) != report_hash:
        raise PackagingError("Chapter mapping or mastering report changed while being read")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("._-") or "Audiobook"
    author_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", author).strip("._-") or "Author"
    names = {"m4b": f"{stem}-{author_stem}.m4b", "zip": f"{stem}-chapter-MP3s.zip", "sample": f"{stem}-sample.mp3"}
    planned_names = [names["m4b"], names["zip"], "README.txt", "delivery-manifest.json"]
    if sample_seconds:
        planned_names.append(names["sample"])
    protected = {input_list.resolve(), report_path.resolve(), Path(__file__).resolve()} | {
                    Path(x[field]["file"]) if field == "m4b_source" else Path(x[field])
                    for x in records for field in ("file", "m4b_source")}
    if any((output_dir / name).resolve() in protected for name in planned_names):
        raise PackagingError("Output would overwrite an input or helper")
    build_plan = {"version": VERSION, "packager_sha256": digest(Path(__file__)),
                  "title": title, "author": author, "narrator": narrator,
                  "sample_seconds": sample_seconds, "m4b_codec": "aac", "m4b_bitrate": AAC_BITRATE,
                  "assembly_source": "verified_lossless_mastered_wav",
                  "chapters": [{name: item[name] for name in ("key", "title", "sha256", "bytes", "source_binding", "m4b_source")} for item in records]}
    build_hash = hashlib.sha256(canonical(build_plan)).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / ".package.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PackagingError("Another packager is running in this output directory") from exc
        cached = check_cache(output_dir, build_hash, records, title, author, ffprobe)
        if cached:
            frozen_inputs(records, input_list, input_hash, report_path, report_hash,
                          report["generation_manifest_sha256"])
            print("Reused verified complete delivery; no audio was encoded.", flush=True)
            return cached
        estimated_pcm_bytes = sum(item["m4b_source"]["declared_pcm_samples"] for item in records) * SAMPLE_BYTES
        estimated_output_bytes = sum(item["bytes"] for item in records) * 1.9
        if shutil.disk_usage(output_dir).free < estimated_pcm_bytes + estimated_output_bytes + 100 * 1024 * 1024:
            raise PackagingError("Insufficient free disk space for temporary PCM and complete delivery")
        with tempfile.TemporaryDirectory(prefix=".package-stage-", dir=output_dir) as temporary:
            stage = Path(temporary)
            joined = stage / "joined.f32"
            cursor = 0
            with joined.open("wb") as output:
                for item in records:
                    before = output.tell()
                    run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", item["m4b_source"]["file"],
                         "-map", "0:a:0", "-vn", "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_f32le",
                         "-f", "f32le", "pipe:1"], stdout=output)
                    size = output.tell() - before
                    if size <= 0 or size % SAMPLE_BYTES:
                        raise PackagingError(f"Invalid decoded sample count: {item['key']}")
                    count = size // SAMPLE_BYTES
                    if count != item["m4b_source"]["declared_pcm_samples"]:
                        raise PackagingError(f"Decoded lossless WAV sample count differs from verified header: {item['key']}")
                    item.update({"decoded_samples": count, "duration_seconds": count / RATE,
                                 "start_sample": cursor, "end_sample": cursor + count,
                                 "start_seconds": cursor / RATE, "end_seconds": (cursor + count) / RATE})
                    cursor += count
                    print(f"Decoded chapter {item['key']}: {count / RATE:.3f} seconds", flush=True)
            if any(digest(Path(item["file"])) != item["sha256"] or
                   digest(Path(item["m4b_source"]["file"])) != item["m4b_source"]["sha256"] for item in records):
                raise PackagingError("Mastered source changed during packaging; no final receipt written")
            metadata = stage / "chapters.ffmetadata"
            build_metadata(metadata, records, title, author, narrator)
            m4b = stage / names["m4b"]
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "f32le",
                 "-ar", str(RATE), "-ac", "1", "-i", str(joined), "-i", str(metadata), "-map", "0:a:0",
                 "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", str(AAC_BITRATE),
                 "-movflags", "+faststart", "-f", "ipod", str(m4b)])
            verification = verify_m4b(m4b, records, title, author, ffprobe)
            sample = None
            sample_probe = None
            if sample_seconds:
                sample = stage / names["sample"]
                excerpt_duration = min(sample_seconds, records[1]["duration_seconds"])
                run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", records[1]["m4b_source"]["file"],
                     "-t", f"{excerpt_duration:.6f}", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k",
                     "-abr", "0", "-compression_level", "2", "-write_xing", "1", "-id3v2_version", "3", "-map_metadata", "-1",
                     "-metadata", f"title={title} — Chapter 1 sample", "-metadata", f"artist={author}",
                     "-metadata", f"comment=Narration: {narrator}. Generated using Gemini 3.8 Flash-Lite TTS.", str(sample)])
                sample_probe = probe(sample, ffprobe)
                sample_stream = audio_stream(sample_probe)
                if (sample_stream.get("codec_name") != "mp3"
                        or int(sample_stream.get("sample_rate", 0)) != RATE
                        or int(sample_stream.get("channels", 0)) != 1):
                    raise PackagingError("Sample codec verification failed")
                if float(sample_probe.get("format", {}).get("duration", 301)) > 300:
                    raise PackagingError("Sample exceeded five minutes")
            readme = stage / "README.txt"
            readme.write_text(readme_text(title, author, narrator, records, sample is not None), encoding="utf-8")
            archive = stage / names["zip"]
            zip_names = [Path(item["file"]).name for item in records] + ["README.txt"]
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
                for item in records:
                    output.write(item["file"], Path(item["file"]).name)
                output.write(readme, "README.txt")
            with zipfile.ZipFile(archive) as source:
                if source.namelist() != zip_names or source.testzip() is not None:
                    raise PackagingError("Chapter ZIP coverage, order, or CRC verification failed")
                for item in records:
                    with source.open(Path(item["file"]).name) as audio:
                        value = hashlib.sha256()
                        for block in iter(lambda: audio.read(1024 * 1024), b""):
                            value.update(block)
                        if value.hexdigest() != item["sha256"]:
                            raise PackagingError(f"Chapter ZIP source hash mismatch: {item['key']}")
            files = [m4b, archive, readme] + ([sample] if sample else [])
            receipt = {"complete": True, "version": VERSION, "build_sha256": build_hash,
                       "created_unix_seconds": time.time(), "title": title, "author": author, "narrator": narrator,
                       "model": "gemini-3.8-flash-lite-tts", "input_list": str(input_list.resolve()),
                       "assembly_source": "verified_lossless_mastered_wav", "aac_encode_passes": 1,
                       "mastering_report": str(report_path.resolve()), "mastering_report_sha256": report_hash,
                       "input_list_sha256": input_hash, "generation_manifest_sha256": report["generation_manifest_sha256"],
                       "m4b_file": m4b.name, "zip_file": archive.name, "chapter_count": len(records),
                       "duration_seconds": cursor / RATE, "duration_hours": cursor / RATE / 3600,
                       "chapters": records, "m4b_verification": verification, "sample_ffprobe": sample_probe,
                       "sample_source": records[1]["m4b_source"] if sample else None,
                       "zip_verification": {"passed": True, "entry_order": zip_names, "source_hashes_verified": True},
                       "outputs": [{"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)} for path in files]}
            frozen_inputs(records, input_list, input_hash, report_path, report_hash,
                          report["generation_manifest_sha256"])
            for path in files:
                os.replace(path, output_dir / path.name)
            save_json(output_dir / "delivery-manifest.json", receipt)
            print(f"Complete delivery: {len(records)} tracks, {cursor / RATE / 3600:.3f} hours", flush=True)
            return receipt


def self_test(report_path: Path, ffmpeg: str, ffprobe: str) -> dict:
    checks = []

    def check(name, condition):
        if not condition:
            raise PackagingError(f"Self-test failed: {name}")
        checks.append(name)

    with tempfile.TemporaryDirectory(prefix="lumen-package-test-") as temporary:
        base = Path(temporary)
        mastered = base / "mastered"
        mastered.mkdir()
        raw = base / "raw"
        raw.mkdir()
        shutil.copyfile(Path(__file__).resolve().parent / "master_audio.py", base / "master_audio.py")
        helper_hash = digest(base / "master_audio.py")
        mapping, entries, generated = {}, [], []
        wav_durations = (1.03, .92, 1.37)
        wav_frequencies = (700, 1100, 1500)
        for index, duration in enumerate((.87, 1.19, .73)):
            key = f"{index:03d}"
            path = mastered / f"chapter-{key}.mp3"
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                 f"sine=frequency={220+index*200}:sample_rate=44100:duration={duration}", "-ac", "1",
                 "-c:a", "libmp3lame", "-b:a", "192k", "-write_xing", "1", str(path)])
            wav = mastered / f"chapter-{key}.wav"
            native = raw / f"chapter-{key}-001.wav"
            # Different lengths AND tones make accidental MP3 assembly detectable.
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                 f"sine=frequency={wav_frequencies[index]}:sample_rate=44100:duration={wav_durations[index]}",
                 "-ac", "1", "-c:a", "pcm_s24le", str(wav)])
            run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(wav),
                 "-c:a", "pcm_s16le", str(native)])
            request = hashlib.sha256(f"fixture-{key}".encode()).hexdigest()
            title = ["Opening", "Chapter 1 = rêve; #one \\ middle", "Closing"][index]
            item_id = f"chapter-{key}-001"
            metadata = {"id": item_id, "chapter": index, "chunk": 1, "request_sha256": request,
                        "audio_sha256": digest(native)}
            save_json(native.with_suffix(".json"), metadata)
            mapping[key] = {"title": title, "paths": [str(native)], "request_sha256": [request]}
            identity = {"coordinator_version": "fixture", "chapter": index, "title": title, "author": "Rob Xie",
                        "book": "LUMEN", "master_audio_sha256": helper_hash,
                        "inputs": [{"path": str(native), "request_sha256": request, "audio_sha256": digest(native), "id": item_id}]}
            identity_hash = hashlib.sha256(canonical(identity)).hexdigest()
            qa = {"master_identity_sha256": identity_hash, "technical_ceilings_and_format_checks_passed": True,
                  "coordinator_checks": {"duration_and_boundary_checks_passed": True}}
            qa_path = mastered / f"chapter-{key}.qa.json"
            save_json(qa_path, qa)
            save_json(mastered / f"chapter-{key}.checkpoint.json",
                      {"identity": identity, "identity_sha256": identity_hash,
                       "output_sha256": {"mp3": digest(path), "lossless_wav": digest(wav), "qa": digest(qa_path)}})
            entries.append({"chapter": key, "mp3": str(path), "lossless_wav": str(wav)})
            generated.append(metadata)
        input_list = base / "chapters.json"
        master_report = mastered / "mastering-report.json"
        save_json(input_list, {"chapters": mapping})
        generation = base / "generation-manifest.json"
        save_json(generation, {"author": "Rob Xie", "items": generated})
        complete_report = {"complete": True, "chapters": entries, "coordinator_version": "fixture",
                           "generation_manifest_sha256": digest(generation)}
        save_json(master_report, complete_report)
        hashes_before = [digest(Path(entry["mp3"])) for entry in entries]
        wav_hashes_before = [digest(Path(entry["lossless_wav"])) for entry in entries]
        result = package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                         title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        check("M4B retains all three chapters in order with escaped Unicode titles", result["m4b_verification"]["passed"] and result["chapter_count"] == 3)
        check("M4B duration follows exact lossless WAV samples rather than distinct MP3 durations", abs(result["duration_seconds"] - sum(wav_durations)) < 1 / RATE)
        check("Every bookmark length equals its verified decoded WAV frame count",
              all(item["decoded_samples"] == round(duration * RATE) == item["m4b_source"]["declared_pcm_samples"]
                  for item, duration in zip(result["chapters"], wav_durations)))
        check("M4B manifest records each lossless path codec and hash",
              result["assembly_source"] == "verified_lossless_mastered_wav" and result["aac_encode_passes"] == 1
              and all(item["m4b_source"]["codec"]["codec_name"] == "pcm_s24le"
                      and item["m4b_source"]["sha256"] == original for item, original in zip(result["chapters"], wav_hashes_before)))
        check("All chapter boundaries are contiguous exact samples", all(a["end_sample"] == b["start_sample"] for a, b in zip(result["chapters"], result["chapters"][1:])))
        check("ZIP preserves every original MP3 byte", result["zip_verification"]["source_hashes_verified"])
        check("Source MP3s remain unchanged", hashes_before == [digest(Path(entry["mp3"])) for entry in entries])
        check("Source lossless WAVs remain unchanged", wav_hashes_before == [digest(Path(entry["lossless_wav"])) for entry in entries])
        decoded = array("f")
        decoded.frombytes(run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i",
                              str(base / "delivery" / result["m4b_file"]), "-map", "0:a:0", "-c:a", "pcm_f32le",
                              "-f", "f32le", "pipe:1"]))
        if sys.byteorder != "little":
            decoded.byteswap()
        observed_frequencies = []
        for chapter in result["chapters"]:
            middle = (chapter["start_sample"] + chapter["end_sample"]) // 2
            window = decoded[middle - RATE // 8:middle + RATE // 8]
            positive_crossings = sum(a <= 0 < b for a, b in zip(window, window[1:]))
            observed_frequencies.append(positive_crossings * RATE / len(window))
        check("Decoded AAC contains the WAV tones in chapter order rather than MP3 tones",
              all(abs(observed - expected) < 10 for observed, expected in zip(observed_frequencies, wav_frequencies)))
        check("Sample is encoded once from the verified lossless Chapter1 master",
              result["sample_source"]["sha256"] == wav_hashes_before[1])
        check("M4B is mono 44.1 kHz AAC", audio_stream(result["m4b_verification"]["ffprobe"])["codec_name"] == "aac")
        check("Sample does not exceed five minutes", float(result["sample_ffprobe"]["format"]["duration"]) <= 300)
        cached = package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                         title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        check("Verified matching delivery is reused without encoding", cached["created_unix_seconds"] == result["created_unix_seconds"])
        (base / "delivery" / result["m4b_file"]).write_bytes(b"tampered")
        repaired = package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                           title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        check("Tampered cached delivery is rebuilt and verified", repaired["m4b_verification"]["passed"] and repaired["created_unix_seconds"] != result["created_unix_seconds"])
        changed = json.loads(json.dumps(mapping))
        changed["001"]["title"] = "Revised Chapter 1"
        save_json(input_list, {"chapters": changed})
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                    title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            stale_title_rejected = True
        else:
            stale_title_rejected = False
        check("Changed chapter title cannot reuse a stale mastering checkpoint", stale_title_rejected)
        changed = json.loads(json.dumps(mapping))
        changed["001"]["request_sha256"][0] = "a" * 64
        save_json(input_list, {"chapters": changed})
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                    title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            stale_request_rejected = True
        else:
            stale_request_rejected = False
        check("Retake request cannot package old mastered audio", stale_request_rejected)
        save_json(input_list, {"chapters": mapping})
        generation_bytes = generation.read_bytes()
        save_json(generation, {"author": "Rob Xie", "items": generated, "retake": True})
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                    title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            stale_generation_rejected = True
        else:
            stale_generation_rejected = False
        check("Changed generation manifest invalidates old complete mastering report", stale_generation_rejected)
        generation.write_bytes(generation_bytes)
        native = Path(mapping["001"]["paths"][0])
        native_bytes = native.read_bytes()
        native.write_bytes(native_bytes + b"changed")
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                    title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            stale_raw_rejected = True
        else:
            stale_raw_rejected = False
        check("Changed raw audio invalidates checkpoint binding despite unchanged request", stale_raw_rejected)
        native.write_bytes(native_bytes)
        stale_wav = Path(entries[1]["lossless_wav"])
        stale_wav_bytes = stale_wav.read_bytes()
        stale_wav.write_bytes(stale_wav_bytes + b"changed")
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1,
                    title="Test LUMEN", author="Rob Xie", sample_seconds=1.0, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            stale_wav_rejected = True
        else:
            stale_wav_rejected = False
        check("Changed lossless master invalidates final-package reuse before encoding", stale_wav_rejected)
        stale_wav.write_bytes(stale_wav_bytes)
        save_json(master_report, {**complete_report, "complete": False})
        try:
            package(input_list, mastered, master_report, base / "delivery", book_chapters=1, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            incomplete_rejected = True
        else:
            incomplete_rejected = False
        check("Incomplete mastering report is rejected even with old package present", incomplete_rejected)
        save_json(master_report, complete_report)
        save_json(input_list, {"chapters": {"000": mapping["000"], "002": mapping["002"]}})
        try:
            package(input_list, mastered, master_report, base / "missing", book_chapters=1, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            missing_rejected = True
        else:
            missing_rejected = False
        check("Missing production chapter cannot become a final package", missing_rejected and not (base / "missing").exists())
        save_json(input_list, {"chapters": {key: mapping[key] for key in ("000", "002", "001")}})
        try:
            package(input_list, mastered, master_report, base / "wrong-order", book_chapters=1, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except PackagingError:
            order_rejected = True
        else:
            order_rejected = False
        check("Out-of-order chapter mapping is rejected", order_rejected)
        input_list.write_text('{"000": {}, "000": {}}', encoding="utf-8")
        try:
            load_json(input_list)
        except PackagingError:
            duplicate_rejected = True
        else:
            duplicate_rejected = False
        check("Duplicate chapter keys are rejected", duplicate_rejected)
        test_result = {"passed": True, "checks": checks, "fixture_result": result,
                       "scope": "Synthetic local fixtures only. No production audio modified; no network calls."}
        save_json(report_path, test_result)
        return test_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-list", type=Path)
    parser.add_argument("--mastered-dir", type=Path, default=Path(__file__).resolve().parent / "mastered")
    parser.add_argument("--mastering-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "delivery")
    parser.add_argument("--title", default="LUMEN")
    parser.add_argument("--author", default="Rob Xie")
    parser.add_argument("--narrator", default="Gemini (Charon AI voice)")
    parser.add_argument("--sample-seconds", type=float, default=299.9, help="0 to omit; at most 300 seconds")
    parser.add_argument("--ffmpeg", default="/opt/homebrew/bin/ffmpeg")
    parser.add_argument("--ffprobe", default="/opt/homebrew/bin/ffprobe")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--test-report", type=Path)
    args = parser.parse_args()
    try:
        if args.self_test:
            if args.input_list:
                parser.error("Self-test must not use production inputs")
            path = args.test_report or Path(__file__).resolve().parent / "package-self-test.json"
            if path.resolve() == Path(__file__).resolve() or path.suffix.lower() != ".json":
                parser.error("Test report must be a separate JSON file")
            if path.exists() and load_json(path).get("scope") != "Synthetic local fixtures only. No production audio modified; no network calls.":
                parser.error("Test report would overwrite an existing file that is not a packaging self-test report")
            result = self_test(path.resolve(), args.ffmpeg, args.ffprobe)
            print(f"Self-test passed: {len(result['checks'])} checks. Report: {path}")
            return 0
        if not args.input_list:
            parser.error("--input-list is required")
        if args.test_report:
            parser.error("--test-report is only for --self-test")
        package(args.input_list.resolve(), args.mastered_dir.resolve(),
                (args.mastering_report or args.mastered_dir / "mastering-report.json").resolve(),
                args.output_dir.resolve(), title=args.title, author=args.author, narrator=args.narrator,
                sample_seconds=args.sample_seconds, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        return 0
    except (PackagingError, OSError, ValueError) as exc:
        print(f"Packaging stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
