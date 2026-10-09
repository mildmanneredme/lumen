#!/usr/bin/env python3
"""Local, reversible audiobook assembly, mastering, and technical QA.

Usage:
  python master_audio.py --self-test
  python master_audio.py --input-list chapters.json --output-dir mastered
  python master_audio.py --qa-only mastered/chapter-01.mp3

Input JSON is an ordered mapping, optionally under "chapters":
  {"01": {"title": "Chapter 1", "paths": ["raw/01-001.wav", "raw/01-002.wav"]}}
Paths are relative to the JSON file. Chunk order is preserved. Only outer chapter
silence is trimmed by default; internal chunk boundaries and pauses are retained.
A gentle oversampled peak limiter is used only when static gain cannot meet the
level targets; --peak-limiter off disables it. No denoising, crossfade, or time
stretching is applied.
One second of digital silence is added at the head and two at the tail. A small
edge guard preserves quiet onsets/decays; measured total edge silence is reported.

RMS means decoded sample RMS, NOT LUFS. The -20 dBFS goal includes the whole
deliverable, including its padding; body RMS is also reported. A conservative
peak cap can leave RMS below the goal. Actual decoded MP3 levels are verified,
and exports are regenerated from the original assembled PCM if attenuation is
needed. Quiet-window measurements exclude generated padding and are not a
noise-floor certification. Both sample peak and a 4x-resampled peak estimate are
reported; this estimate is not a standardized true-peak meter certification.

Requires numpy and local ffmpeg/ffprobe. Never reads keys or calls an API.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np


RATE = 44100
BLOCK = 441000
FLOAT = np.dtype("<f4")


class MasteringError(RuntimeError):
    pass


@dataclass
class Settings:
    ffmpeg: str = "/opt/homebrew/bin/ffmpeg"
    ffprobe: str = "/opt/homebrew/bin/ffprobe"
    target_rms_db: float = -20.0
    peak_ceiling_db: float = -3.2
    codec_headroom_db: float = 0.6
    max_gain_db: float = 30.0
    head_seconds: float = 1.0
    tail_seconds: float = 2.0
    silence_db: float = -60.0
    long_silence_seconds: float = 2.0
    quiet_window_seconds: float = 0.5
    quiet_window_db: float = -50.0
    trim_edges: bool = True
    edge_guard_seconds: float = 0.02
    max_attempts: int = 8
    peak_limiter: str = "auto"
    max_limiter_overdrive_db: float = 6.0


def db(value: float) -> float | None:
    return 20.0 * math.log10(value) if value > 0 else None


def linear(value_db: float) -> float:
    return 10.0 ** (value_db / 20.0)


def run(command: list[str], *, stdout=None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stdout or subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise MasteringError(f"Cannot run {command[0]}: {exc}") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise MasteringError(f"{Path(command[0]).name} failed: {message}")
    return result


def probe(path: Path, settings: Settings) -> dict:
    result = run([settings.ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                  "stream=codec_name,sample_rate,channels,bit_rate,bits_per_sample,bits_per_raw_sample:format=duration,bit_rate",
                  "-of", "json", str(path)])
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise MasteringError(f"No audio stream: {path}")
    return data


def decode_to(path: Path, output, settings: Settings) -> None:
    run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_f32le",
         "-f", "f32le", "pipe:1"], stdout=output)


def native_pcm_qa(path: Path) -> dict:
    """Inspect native integer samples before resampling; rail hits need review."""
    try:
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2:
                return {"status": "not_measured", "reason": "Native check supports 16-bit PCM WAV only"}
            minimum, maximum, rail_hits, count = 32767, -32768, 0, 0
            while block := source.readframes(BLOCK):
                values = np.frombuffer(block, dtype="<i2")
                minimum = min(minimum, int(values.min()))
                maximum = max(maximum, int(values.max()))
                rail_hits += int(np.count_nonzero((values == -32768) | (values == 32767)))
                count += len(values)
            peak = max(abs(minimum), abs(maximum)) / 32768 if count else 0
            return {"status": "measured", "samples": count,
                    "sample_peak_dbfs": db(peak), "minimum_integer_sample": minimum if count else None,
                    "maximum_integer_sample": maximum if count else None,
                    "samples_at_integer_rails": rail_hits,
                    "scope": "Native samples before resampling; rail hits are review candidates, not proof of audible clipping"}
    except (wave.Error, EOFError):
        return {"status": "not_measured", "reason": "Source is not a supported PCM WAV"}


def oversampled_peak(pcm: Path, settings: Settings) -> float:
    """Stream a 4x resample; never keep the enlarged waveform in memory."""
    command = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "f32le",
               "-ar", str(RATE), "-ac", "1", "-i", str(pcm), "-af", "aresample=176400",
               "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError as exc:
        raise MasteringError(f"Cannot run {settings.ffmpeg}: {exc}") from exc
    peak = 0.0
    try:
        while block := process.stdout.read(BLOCK * FLOAT.itemsize):
            if len(block) % FLOAT.itemsize:
                raise MasteringError("Invalid oversampled PCM byte count")
            values = np.frombuffer(block, dtype=FLOAT)
            if not np.isfinite(values).all():
                raise MasteringError("Non-finite oversampled PCM")
            peak = max(peak, float(np.max(np.abs(values))))
        error = process.stderr.read().decode("utf-8", errors="replace").strip()
        if process.wait():
            raise MasteringError(f"Oversampled peak measurement failed: {error}")
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return peak


def limit_body(source: Path, output: Path, settings: Settings, ceiling_db: float) -> str:
    # Fixed working-domain scaling keeps alimiter's limit within its supported
    # range. The final volume only reverses that scale; it is not RMS makeup.
    scale = 0.5 / linear(ceiling_db)
    filters = (f"volume={scale:.12g}:precision=double,aresample=176400,"
               "alimiter=limit=0.5:attack=5:release=50:level=false:latency=true,"
               f"volume={1 / scale:.12g}:precision=double,aresample=44100")
    run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "f32le",
         "-ar", str(RATE), "-ac", "1", "-i", str(source), "-af", filters,
         "-c:a", "pcm_f32le", "-f", "f32le", str(output)])
    if output.stat().st_size != source.stat().st_size:
        raise MasteringError("Limiter/resampler changed body sample count; refusing cadence change")
    return filters


def pcm_map(path: Path) -> np.memmap:
    size = path.stat().st_size
    if not size or size % FLOAT.itemsize:
        raise MasteringError(f"Empty or invalid decoded PCM: {path.name}")
    return np.memmap(path, dtype=FLOAT, mode="r")


def sample_stats(samples, start: int = 0, end: int | None = None) -> dict:
    end = len(samples) if end is None else min(end, len(samples))
    if start < 0 or end <= start:
        return {"samples": 0, "rms_dbfs": None, "sample_peak_dbfs": None,
                "sample_peak_linear": 0.0, "samples_at_or_above_full_scale": 0, "energy": 0.0}
    energy = 0.0
    peak = 0.0
    rail_hits = 0
    for offset in range(start, end, BLOCK):
        block = np.asarray(samples[offset:min(offset + BLOCK, end)], dtype=np.float64)
        if not np.isfinite(block).all():
            raise MasteringError("Audio contains non-finite samples")
        energy += float(np.dot(block, block))
        peak = max(peak, float(np.max(np.abs(block))))
        rail_hits += int(np.count_nonzero(np.abs(block) >= 1.0))
    count = end - start
    return {"samples": count, "rms_dbfs": db(math.sqrt(energy / count)),
            "sample_peak_dbfs": db(peak), "sample_peak_linear": peak,
            "samples_at_or_above_full_scale": rail_hits, "energy": energy}


def frame_rms(samples, frame_samples: int = 441) -> np.ndarray:
    # Bounded working memory; the resulting 10 ms measurement grid is small.
    count = (len(samples) + frame_samples - 1) // frame_samples
    values = np.empty(count, dtype=np.float64)
    frames_per_block = max(1, BLOCK // frame_samples)
    for frame in range(0, count, frames_per_block):
        first = frame * frame_samples
        last = min(len(samples), (frame + frames_per_block) * frame_samples)
        block = np.asarray(samples[first:last], dtype=np.float64)
        full = len(block) // frame_samples
        if full:
            square = block[:full * frame_samples].reshape(full, frame_samples)
            values[frame:frame + full] = np.sqrt(np.mean(square * square, axis=1))
        if len(block) % frame_samples:
            tail = block[full * frame_samples:]
            values[frame + full] = math.sqrt(float(np.dot(tail, tail)) / len(tail))
    return values


def quiet_measurements(samples, settings: Settings, start: int, end: int) -> dict:
    window = max(1, round(settings.quiet_window_seconds * RATE))
    records = []
    threshold = linear(settings.quiet_window_db)
    for first in range(start, end - window + 1, window):
        block = np.asarray(samples[first:first + window], dtype=np.float64)
        rms = math.sqrt(float(np.dot(block, block)) / window)
        if rms <= threshold:
            records.append({"start_seconds": first / RATE, "end_seconds": (first + window) / RATE,
                            "rms_dbfs": db(rms), "sample_peak_dbfs": db(float(np.max(np.abs(block)))),
                            "exact_zero_fraction": float(np.count_nonzero(block == 0)) / window})
    finite = [item["rms_dbfs"] for item in records if item["rms_dbfs"] is not None]
    return {"scope": "Non-overlapping windows within the unpadded body; these may contain pauses or quiet speech. Not noise-floor certification.",
            "window_seconds": window / RATE, "selection_rms_at_or_below_dbfs": settings.quiet_window_db,
            "body_start_seconds": start / RATE, "body_end_seconds": end / RATE,
            "status": "measured_quiet_windows" if records else "insufficient_quiet_windows",
            "count": len(records), "digital_zero_windows": sum(item["rms_dbfs"] is None for item in records),
            "finite_rms_min_dbfs": min(finite) if finite else None,
            "finite_rms_median_dbfs": float(np.median(finite)) if finite else None,
            "finite_rms_max_dbfs": max(finite) if finite else None, "windows": records}


def pcm_qa(samples, settings: Settings, *, head_samples: int = 0, tail_samples: int = 0) -> dict:
    stats = sample_stats(samples)
    stats.pop("energy")
    frame = 441
    silent = frame_rms(samples, frame) <= linear(settings.silence_db)
    boundaries = np.flatnonzero(np.diff(np.r_[False, silent, False].astype(np.int8)))
    leading = trailing = 0.0
    internal = []
    for first, last in zip(boundaries[::2], boundaries[1::2]):
        first_sample, last_sample = int(first) * frame, min(int(last) * frame, len(samples))
        start, end = first_sample / RATE, last_sample / RATE
        if first_sample == 0:
            leading = end
        if last_sample == len(samples):
            trailing = end - start
        if first_sample > 0 and last_sample < len(samples) and end - start >= settings.long_silence_seconds:
            internal.append({"start_seconds": start, "end_seconds": end, "duration_seconds": end - start})
    body_end = max(head_samples, len(samples) - tail_samples)
    body = sample_stats(samples, head_samples, body_end)
    body.pop("energy")
    stats.update({"duration_seconds": len(samples) / RATE, "analysis_sample_rate": RATE, "analysis_channels": 1,
                  "peak_scope": "Decoded sample peak, not oversampled true peak",
                  "clipping_scope": "Float PCM samples at or above full scale; zero hits cannot prove that source audio was never clipped",
                  "silence_measurement": {"frame_seconds": frame / RATE, "rms_threshold_dbfs": settings.silence_db,
                                          "leading_seconds": leading, "trailing_seconds": trailing,
                                          "internal_minimum_seconds": settings.long_silence_seconds,
                                          "long_internal_regions": internal},
                  "unpadded_body": body,
                  "quiet_window_measurements": quiet_measurements(samples, settings, head_samples, body_end)})
    return stats


def mp3_frames(path: Path) -> dict:
    """Check MPEG-1 Layer III headers, including the Info/Xing header frame."""
    rates = [44100, 48000, 32000]
    bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    found_rates, found_bitrates, channel_modes = set(), set(), set()
    count = 0
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(0)
        tag = source.read(10)
        offset = 0
        if tag[:3] == b"ID3":
            if len(tag) < 10 or any(byte & 0x80 for byte in tag[6:10]):
                raise MasteringError("Malformed MP3 ID3 header")
            tag_size = sum(int(byte) << (7 * (3 - index)) for index, byte in enumerate(tag[6:10]))
            offset = 10 + tag_size + (10 if tag[3] == 4 and tag[5] & 0x10 else 0)
        stop = size
        if size >= 128:
            source.seek(size - 128)
            if source.read(3) == b"TAG":
                stop -= 128
        if offset > stop:
            raise MasteringError("Malformed MP3 ID3 header")
        while offset < stop:
            if stop - offset < 4:
                raise MasteringError("Unexpected trailing bytes in MP3")
            source.seek(offset)
            raw = source.read(4)
            if len(raw) != 4:
                raise MasteringError("Truncated MP3 frame")
            header = int.from_bytes(raw, "big")
            if (header >> 21) != 0x7FF or ((header >> 19) & 3) != 3 or ((header >> 17) & 3) != 1:
                raise MasteringError(f"Invalid MPEG-1 Layer III frame at byte {offset}")
            bitrate = bitrates[(header >> 12) & 15]
            rate_index = (header >> 10) & 3
            if not bitrate or rate_index == 3:
                raise MasteringError("Unsupported MP3 frame rate/bitrate")
            rate = rates[rate_index]
            length = (144000 * bitrate) // rate + ((header >> 9) & 1)
            if offset + length > stop:
                raise MasteringError("Truncated MP3 frame")
            found_rates.add(rate)
            found_bitrates.add(bitrate)
            channel_modes.add((header >> 6) & 3)
            offset += length
            count += 1
    return {"frame_count": count, "frame_bitrates_kbps": sorted(found_bitrates),
            "frame_sample_rates": sorted(found_rates), "channel_modes": sorted(channel_modes),
            "cbr_192kbps_verified": count > 0 and found_bitrates == {192},
            "mono_44100_verified": found_rates == {RATE} and channel_modes == {3}}


def analyze_audio(path: Path, settings: Settings, *, head_samples: int = 0, tail_samples: int = 0) -> dict:
    information = probe(path, settings)
    with tempfile.TemporaryDirectory(prefix="lumen-audio-qa-") as temporary:
        decoded = Path(temporary) / "decoded.f32"
        with decoded.open("wb") as output:
            decode_to(path, output, settings)
        samples = pcm_map(decoded)
        result = pcm_qa(samples, settings, head_samples=head_samples, tail_samples=tail_samples)
        del samples
        result["oversampled_peak_4x_dbfs"] = db(oversampled_peak(decoded, settings))
        result["oversampled_peak_scope"] = "4x ffmpeg resampling peak estimate; not a standardized true-peak meter certification"
    result["probe"] = information
    result["analysis_conversion"] = "First audio stream decoded to mono 44.1 kHz float PCM; exported MP3 must already match this format."
    if path.suffix.lower() == ".mp3":
        result["mp3_frame_check"] = mp3_frames(path)
    return result


def write_pcm(path: Path, samples, first: int, last: int, gain: float, head: int, tail: int) -> None:
    with path.open("wb") as output:
        output.write(np.zeros(head, dtype=FLOAT).tobytes())
        for offset in range(first, last, BLOCK):
            block = np.asarray(samples[offset:min(offset + BLOCK, last)], dtype=np.float64)
            output.write(np.asarray(block * gain, dtype=FLOAT).tobytes())
        output.write(np.zeros(tail, dtype=FLOAT).tobytes())


def export(pcm: Path, wav: Path, mp3: Path, settings: Settings, title: str) -> None:
    base = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "f32le",
            "-ar", str(RATE), "-ac", "1", "-i", str(pcm), "-map_metadata", "-1"]
    run(base + ["-c:a", "pcm_s24le", str(wav)])
    run(base + ["-c:a", "libmp3lame", "-b:a", "192k", "-abr", "0", "-compression_level", "2",
                "-write_xing", "1", "-id3v2_version", "3", "-metadata", f"title={title}", str(mp3)])


def master_chapter(paths: list[Path], title: str, directory: Path, settings: Settings) -> tuple[Path, Path, dict]:
    joined = directory / "joined.f32"
    source_records = []
    with joined.open("wb") as output:
        for path in paths:
            information = probe(path, settings)
            before = output.tell()
            decode_to(path, output, settings)
            source_records.append({"path": str(path), "probe": information,
                                   "native_pcm_qa": native_pcm_qa(path),
                                   "decoded_samples": (output.tell() - before) // FLOAT.itemsize})
    samples = pcm_map(joined)
    original = sample_stats(samples)
    if original["rms_dbfs"] is None:
        del samples
        raise MasteringError("Chapter contains only digital silence; no narration to master")
    original["oversampled_peak_4x_dbfs"] = db(oversampled_peak(joined, settings))
    preliminary_gain_db = min(settings.target_rms_db - original["rms_dbfs"], settings.max_gain_db,
                              settings.peak_ceiling_db - settings.codec_headroom_db - original["oversampled_peak_4x_dbfs"])
    first, last = 0, len(samples)
    if settings.trim_edges:
        active = np.flatnonzero(frame_rms(samples) * linear(preliminary_gain_db) > linear(settings.silence_db))
        if len(active):
            guard = round(settings.edge_guard_seconds * RATE)
            first = max(0, int(active[0]) * 441 - guard)
            last = min(len(samples), (int(active[-1]) + 1) * 441 + guard)
    body = sample_stats(samples, first, last)
    head, tail = round(settings.head_seconds * RATE), round(settings.tail_seconds * RATE)
    padded_rms = db(math.sqrt(body["energy"] / (body["samples"] + head + tail)))
    desired_gain_db = settings.target_rms_db - 0.05 - padded_rms
    limiter_ceiling_db = settings.peak_ceiling_db - settings.codec_headroom_db
    peak_gain_db = limiter_ceiling_db - original["oversampled_peak_4x_dbfs"]
    limiter_used = settings.peak_limiter == "auto" and peak_gain_db < desired_gain_db
    gain_limit_db = peak_gain_db + (settings.max_limiter_overdrive_db if limiter_used else 0)
    gain_db = min(desired_gain_db, gain_limit_db, settings.max_gain_db)
    pcm, wav, mp3 = directory / "master.f32", directory / "master.wav", directory / "master.mp3"
    pre_limit, limited = directory / "pre-limit.f32", directory / "limited.f32"
    attempts = []
    limiter_filter = None
    for attempt in range(1, settings.max_attempts + 1):
        if limiter_used:
            write_pcm(pre_limit, samples, first, last, linear(gain_db), 0, 0)
            limiter_filter = limit_body(pre_limit, limited, settings, limiter_ceiling_db)
            limited_samples = pcm_map(limited)
            write_pcm(pcm, limited_samples, 0, len(limited_samples), 1, head, tail)
            del limited_samples
        else:
            write_pcm(pcm, samples, first, last, linear(gain_db), head, tail)
        master_qa = pcm_qa(pcm_map(pcm), settings, head_samples=head, tail_samples=tail)
        master_qa["oversampled_peak_4x_dbfs"] = db(oversampled_peak(pcm, settings))
        export(pcm, wav, mp3, settings, title)
        qa = analyze_audio(mp3, settings, head_samples=head, tail_samples=tail)
        if qa["rms_dbfs"] is None or qa["sample_peak_dbfs"] is None:
            del samples
            raise MasteringError("Encoded MP3 contains only digital silence; source may be below the maximum-gain limit")
        attempts.append({"attempt": attempt, "gain_db": gain_db, "limiter_ceiling_dbfs": limiter_ceiling_db if limiter_used else None,
                         "master_rms_dbfs": master_qa["rms_dbfs"], "rms_dbfs": qa["rms_dbfs"],
                         "sample_peak_dbfs": qa["sample_peak_dbfs"], "oversampled_peak_4x_dbfs": qa["oversampled_peak_4x_dbfs"]})
        rms_excess = max(0.0, qa["rms_dbfs"] - settings.target_rms_db, master_qa["rms_dbfs"] - settings.target_rms_db)
        peak_excess = max(0.0, qa["sample_peak_dbfs"] - settings.peak_ceiling_db,
                          qa["oversampled_peak_4x_dbfs"] - settings.peak_ceiling_db,
                          master_qa["sample_peak_dbfs"] - settings.peak_ceiling_db,
                          master_qa["oversampled_peak_4x_dbfs"] - settings.peak_ceiling_db)
        if peak_excess > 0 and limiter_used:
            limiter_ceiling_db -= peak_excess + 0.10
            gain_limit_db = limiter_ceiling_db - original["oversampled_peak_4x_dbfs"] + settings.max_limiter_overdrive_db
            gain_db = min(gain_db, gain_limit_db)
            if rms_excess:
                gain_db -= rms_excess + 0.03
            continue
        if rms_excess > 0 or peak_excess > 0:
            gain_db -= max(rms_excess + 0.03 if rms_excess else 0, peak_excess + 0.08 if peak_excess else 0)
            continue
        # Calibrate before the limiter, never apply unsafe makeup after it.
        # WAV RMS remains <= target even when the codec attenuates its output.
        master_deficit = settings.target_rms_db - 0.05 - master_qa["rms_dbfs"]
        allowed_gain_db = min(gain_limit_db, settings.max_gain_db)
        if limiter_used and qa["rms_dbfs"] < settings.target_rms_db - 0.5 and master_deficit > 0.03 and gain_db < allowed_gain_db - 0.02:
            gain_db += min(master_deficit, allowed_gain_db - gain_db)
            continue
        if rms_excess <= 0 and peak_excess <= 0:
            break
    else:
        del samples
        raise MasteringError("Decoded MP3 level verification failed after bounded retries")
    frame_check = qa["mp3_frame_check"]
    stream = qa["probe"]["streams"][0]
    if not (frame_check["cbr_192kbps_verified"] and frame_check["mono_44100_verified"] and
            stream["codec_name"] == "mp3" and int(stream["sample_rate"]) == RATE and int(stream["channels"]) == 1):
        del samples
        raise MasteringError("Exported MP3 failed mono/44.1 kHz/192 kbps CBR verification")
    warnings = []
    if qa["rms_dbfs"] < settings.target_rms_db - 0.5:
        warnings.append("RMS target remains constrained by peak ceiling, gentle-limiter budget, or maximum gain; inspect the reported deficit")
    if qa["rms_dbfs"] < -23:
        warnings.append("Decoded RMS is below -23 dBFS; review the peak-constrained result before use")
    if original["samples_at_or_above_full_scale"]:
        warnings.append("Decoded resampled PCM exceeds full scale; interpolation can cause overshoot. Check native sample measurements before inferring source clipping")
    if any(r["native_pcm_qa"].get("samples_at_integer_rails", 0) for r in source_records):
        warnings.append("Native source PCM reaches integer rails; inspect those peaks for possible clipping")
    if qa["silence_measurement"]["long_internal_regions"]:
        warnings.append("Long internal quiet regions retained for cadence; review their timestamps")
    for label, expected in (("leading_seconds", settings.head_seconds), ("trailing_seconds", settings.tail_seconds)):
        if abs(qa["silence_measurement"][label] - expected) > 0.15:
            warnings.append(f"Measured {label} differs from padding target by more than 0.15 seconds")
    result = {"title": title, "sources": source_records,
              "source_pcm": {key: value for key, value in original.items() if key != "energy"},
              "assembly": {"chunk_order_preserved": True, "internal_silence_removed": False,
                           "source_samples": len(samples), "body_samples": last - first,
                           "leading_source_samples_trimmed": first, "trailing_source_samples_trimmed": len(samples) - last,
                           "added_head_samples": head, "added_tail_samples": tail,
                           "edge_guard_seconds": settings.edge_guard_seconds if settings.trim_edges else 0},
              "normalization": {"method": "Chapter-wide gain with gentle oversampled peak limiting" if limiter_used else "One static gain for the complete chapter",
                                "target_full_file_rms_dbfs": settings.target_rms_db,
                                "sample_peak_ceiling_dbfs": settings.peak_ceiling_db,
                                "provisional_codec_headroom_db": settings.codec_headroom_db,
                                "applied_gain_db": gain_db, "static_peak_constrained": peak_gain_db < desired_gain_db,
                                "peak_constrained": qa["rms_dbfs"] < settings.target_rms_db - 0.5 and gain_limit_db < desired_gain_db,
                                "gain_cap_constrained": settings.max_gain_db < desired_gain_db,
                                "decoded_rms_deficit_db": settings.target_rms_db - qa["rms_dbfs"], "attempts": attempts},
              "limiter": {"mode": settings.peak_limiter, "used": limiter_used, "filter": limiter_filter,
                          "ceiling_dbfs": limiter_ceiling_db if limiter_used else None,
                          "maximum_permitted_pre_limiter_peak_overdrive_db": settings.max_limiter_overdrive_db,
                          "measured_source_4x_peak_overdrive_db": max(0.0, original["oversampled_peak_4x_dbfs"] + gain_db - limiter_ceiling_db) if limiter_used else 0.0,
                          "scope": "Peak overdrive is measured from source peak and pre-gain; it is not an exact gain-reduction statistic"},
              "master_pcm_qa": master_qa, "decoded_mp3_qa": qa, "warnings": warnings,
              "technical_ceilings_and_format_checks_passed": True,
              "rms_target_met_within_0_5_db": settings.target_rms_db - 0.5 <= qa["rms_dbfs"] <= settings.target_rms_db,
              "scope": "Technical measurements only; not listening approval, noise-floor certification, or publishing acceptance"}
    del samples
    return wav, mp3, result


def load_manifest(path: Path) -> list[dict]:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MasteringError(f"Cannot read input-list JSON: {exc}") from exc
    chapters = content.get("chapters", content) if isinstance(content, dict) else None
    if not isinstance(chapters, dict) or not chapters:
        raise MasteringError("Input list must be a nonempty chapter-to-{paths} JSON mapping")
    records, used_names = [], set()
    for key, entry in chapters.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("paths"), list) or not entry["paths"]:
            raise MasteringError(f"Chapter {key!r} needs a nonempty paths array")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(key)).strip("._-")
        if not name or name in used_names:
            raise MasteringError(f"Invalid or colliding chapter file name: {key!r}")
        used_names.add(name)
        paths = []
        for item in entry["paths"]:
            if not isinstance(item, str) or not item:
                raise MasteringError(f"Chapter {key!r} contains an invalid path")
            source = (path.parent / item).resolve()
            if source.suffix.lower() != ".wav" or not source.is_file():
                raise MasteringError(f"Input must be an existing WAV: {source}")
            paths.append(source)
        records.append({"key": str(key), "name": name, "title": str(entry.get("title", f"Chapter {key}")), "paths": paths})
    return records


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process_manifest(input_list: Path, output_dir: Path, report: Path, settings: Settings, overwrite: bool) -> dict:
    chapters = load_manifest(input_list)
    inputs = {path for chapter in chapters for path in chapter["paths"]}
    destinations = [output_dir / f"chapter-{chapter['name']}{suffix}" for chapter in chapters for suffix in (".mp3", ".wav", ".qa.json")]
    if any(path.resolve() in inputs for path in destinations + [report]):
        raise MasteringError("Output destination would overwrite a source WAV")
    if report.resolve() == input_list.resolve() or report.resolve() in {path.resolve() for path in destinations}:
        raise MasteringError("Aggregate report would overwrite the input manifest or a chapter output")
    if not overwrite and any(path.exists() for path in destinations + [report]):
        raise MasteringError("Output already exists; use a new output directory or explicit --overwrite")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"input_list": str(input_list.resolve()), "output_dir": str(output_dir.resolve()), "chapters": [], "complete": False}
    for chapter in chapters:
        with tempfile.TemporaryDirectory(prefix=".mastering-", dir=output_dir) as temporary:
            wav, mp3, result = master_chapter(chapter["paths"], chapter["title"], Path(temporary), settings)
            wav_final = output_dir / f"chapter-{chapter['name']}.wav"
            mp3_final = output_dir / f"chapter-{chapter['name']}.mp3"
            qa_final = output_dir / f"chapter-{chapter['name']}.qa.json"
            result["outputs"] = {"lossless_wav": str(wav_final.resolve()), "mp3": str(mp3_final.resolve())}
            os.replace(wav, wav_final)
            os.replace(mp3, mp3_final)
            save_json(qa_final, result)
        summary["chapters"].append({"chapter": chapter["key"], "mp3": str(mp3_final.resolve()), "qa": str(qa_final.resolve()),
                                    "rms_dbfs": result["decoded_mp3_qa"]["rms_dbfs"],
                                    "sample_peak_dbfs": result["decoded_mp3_qa"]["sample_peak_dbfs"], "warnings": result["warnings"]})
        save_json(report, summary)
        print(f"Chapter {chapter['key']}: RMS {result['decoded_mp3_qa']['rms_dbfs']:.2f} dBFS; peak {result['decoded_mp3_qa']['sample_peak_dbfs']:.2f} dBFS", flush=True)
    summary["complete"] = True
    save_json(report, summary)
    return summary


def write_test_wav(path: Path, rate: int, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.rint(np.clip(samples, -1, 32767 / 32768) * 32768).astype("<i2").tobytes())


def tone(rate: int, seconds: float, amplitude: float, frequency: float) -> np.ndarray:
    return amplitude * np.sin(2 * np.pi * frequency * np.arange(round(rate * seconds), dtype=np.float64) / rate)


def self_test(settings: Settings, report: Path) -> dict:
    checks = []
    def check(name: str, condition: bool) -> None:
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise MasteringError(f"Self-test failed: {name}")
    with tempfile.TemporaryDirectory(prefix="lumen-master-self-test-") as temporary:
        directory = Path(temporary)
        known = sample_stats(tone(RATE, 2, 0.1, 440))
        check("known sine sample RMS formula", abs(known["rms_dbfs"] - (-23.0102999566398)) < 1e-6)
        check("full-scale sample indicators", sample_stats(np.array([-1.0, 1.0, 0.5]))["samples_at_or_above_full_scale"] == 2)
        empty = sample_stats(np.zeros(100))
        check("digital silence produces null RMS without NaN", empty["rms_dbfs"] is None)
        chunk1, chunk2 = directory / "chunk1.wav", directory / "chunk2.wav"
        write_test_wav(chunk1, 24000, np.r_[np.zeros(7200), tone(24000, 3, 0.02, 220), np.zeros(60000), tone(24000, 1, 0.03, 440)])
        write_test_wav(chunk2, 48000, np.r_[tone(48000, 1, 0.015, 660), np.zeros(16800)])
        (directory / "normal").mkdir()
        wav, _, normal = master_chapter([chunk1, chunk2], "Synthetic test", directory / "normal", settings)
        qa = normal["decoded_mp3_qa"]
        check("decoded MP3 meets RMS ceiling and near target", settings.target_rms_db - 0.5 <= qa["rms_dbfs"] <= settings.target_rms_db)
        check("decoded MP3 meets sample peak ceiling", qa["sample_peak_dbfs"] <= settings.peak_ceiling_db)
        check("decoded MP3 has zero full-scale hits", qa["samples_at_or_above_full_scale"] == 0)
        check("actual frames are mono 44.1 kHz 192 kbps CBR", qa["mp3_frame_check"]["cbr_192kbps_verified"] and qa["mp3_frame_check"]["mono_44100_verified"])
        check("long intentional internal pause retained", any(2.45 <= item["duration_seconds"] <= 2.55 for item in qa["silence_measurement"]["long_internal_regions"]))
        check("measured edge silence near one and two seconds", abs(qa["silence_measurement"]["leading_seconds"] - 1) < 0.08 and abs(qa["silence_measurement"]["trailing_seconds"] - 2) < 0.08)
        with wave.open(str(wav), "rb") as source:
            payload = source.readframes(source.getnframes())
            check("lossless master is mono 44.1 kHz 24 bit", source.getnchannels() == 1 and source.getframerate() == RATE and source.getsampwidth() == 3)
        check("exact added WAV padding preserved", payload[:RATE * 3] == bytes(RATE * 3) and payload[-2 * RATE * 3:] == bytes(2 * RATE * 3))
        check("quiet-window measurement excludes added padding", qa["quiet_window_measurements"]["body_start_seconds"] == 1 and math.isclose(qa["quiet_window_measurements"]["body_end_seconds"], qa["duration_seconds"] - 2, abs_tol=1e-9))
        impulse = tone(RATE, 3, 0.005, 440)
        impulse[RATE] = 0.95
        high_crest = directory / "high-crest.wav"
        write_test_wav(high_crest, RATE, impulse)
        (directory / "crest").mkdir()
        _, _, crest = master_chapter([high_crest], "High crest test", directory / "crest", settings)
        check("high crest reports peak-constrained RMS deficit", crest["normalization"]["peak_constrained"] and crest["normalization"]["decoded_rms_deficit_db"] > 5)
        check("high crest MP3 cannot overshoot ceiling", crest["decoded_mp3_qa"]["sample_peak_dbfs"] <= settings.peak_ceiling_db)
        # Quiet voiced harmonics with a retained pause and sparse isolated spikes.
        # Sustained material should receive gain, not broad dynamic compression.
        elapsed = np.arange(20 * RATE, dtype=np.float64) / RATE
        envelope = 0.45 + 0.55 * np.sin(2 * np.pi * 0.7 * elapsed) ** 2
        speechlike = 0.09 * envelope * (np.sin(2 * np.pi * 180 * elapsed) +
                                       0.18 * np.sin(2 * np.pi * 360 * elapsed) +
                                       0.08 * np.sin(2 * np.pi * 720 * elapsed))
        speechlike[7 * RATE:round(9.25 * RATE)] = 0
        for index, instant in enumerate((2.2, 5.3, 11.1, 14.7, 17.2)):
            speechlike[round(instant * RATE)] = 0.45 * (-1 if index % 2 else 1)
        voiced = directory / "voiced-spikes.wav"
        write_test_wav(voiced, RATE, np.r_[np.zeros(round(0.2 * RATE)), speechlike, np.zeros(round(0.25 * RATE))])
        (directory / "voiced").mkdir()
        _, _, gentle = master_chapter([voiced], "Voiced spikes test", directory / "voiced", settings)
        gentle_qa = gentle["decoded_mp3_qa"]
        check("speechlike sparse-spike fixture uses gentle limiter", gentle["limiter"]["used"])
        check("limited fixture meets decoded RMS target interval", settings.target_rms_db - 0.5 <= gentle_qa["rms_dbfs"] <= settings.target_rms_db)
        check("limited fixture WAV RMS cannot overshoot target", gentle["master_pcm_qa"]["rms_dbfs"] <= settings.target_rms_db)
        check("limited fixture meets decoded sample and 4x peak ceilings", max(gentle_qa["sample_peak_dbfs"], gentle_qa["oversampled_peak_4x_dbfs"]) <= settings.peak_ceiling_db)
        check("limited fixture has zero full-scale hits", gentle_qa["samples_at_or_above_full_scale"] == 0)
        check("limiter pre-peak overdrive stays within gentle budget", gentle["limiter"]["measured_source_4x_peak_overdrive_db"] <= settings.max_limiter_overdrive_db + 1e-6)
        check("limiter leaves exact chapter sample duration", gentle_qa["samples"] == gentle["assembly"]["body_samples"] + 3 * RATE)
        check("limiter preserves intentional pause timing", any(abs(item["duration_seconds"] - 2.25) <= 0.03 for item in gentle_qa["silence_measurement"]["long_internal_regions"]))
        source_pcm = pcm_map(directory / "voiced" / "joined.f32")
        mastered_pcm = pcm_map(directory / "voiced" / "master.f32")
        source_first = round(4.2 * RATE)
        source_last = source_first + RATE
        mastered_first = RATE + source_first - gentle["assembly"]["leading_source_samples_trimmed"]
        measured_voice_gain = sample_stats(mastered_pcm, mastered_first, mastered_first + RATE)["rms_dbfs"] - sample_stats(source_pcm, source_first, source_last)["rms_dbfs"]
        check("sustained voice outside spikes is not compressed", abs(measured_voice_gain - gentle["normalization"]["applied_gain_db"]) < 0.03)
        del source_pcm, mastered_pcm
        zeros = directory / "zeros.wav"
        write_test_wav(zeros, RATE, np.zeros(RATE))
        (directory / "zeros").mkdir()
        try:
            master_chapter([zeros], "Silence", directory / "zeros", settings)
        except MasteringError:
            silence_rejected = True
        else:
            silence_rejected = False
        check("all-silent chapter rejected", silence_rejected)
        invalid = directory / "invalid.json"
        invalid.write_text('{"01":{"paths":[]}}', encoding="utf-8")
        try:
            load_manifest(invalid)
        except MasteringError:
            manifest_rejected = True
        else:
            manifest_rejected = False
        check("empty manifest path list rejected", manifest_rejected)
        manifest = directory / "chapters.json"
        manifest.write_text(json.dumps({"01": {"paths": ["chunk1.wav", "chunk2.wav"]}}), encoding="utf-8")
        loaded = load_manifest(manifest)
        check("relative input paths and chunk order preserved", loaded[0]["paths"] == [chunk1.resolve(), chunk2.resolve()])
        hashes_before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (chunk1, chunk2)]
        output_directory = directory / "manifest-output"
        aggregate_report = output_directory / "mastering-report.json"
        process_manifest(manifest, output_directory, aggregate_report, settings, False)
        check("manifest mastering creates WAV MP3 and QA", all((output_directory / f"chapter-01{suffix}").is_file() for suffix in (".wav", ".mp3", ".qa.json")))
        check("manifest mastering preserves raw inputs", hashes_before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (chunk1, chunk2)])
        try:
            process_manifest(manifest, output_directory, aggregate_report, settings, False)
        except MasteringError:
            overwrite_rejected = True
        else:
            overwrite_rejected = False
        check("existing exports protected without overwrite flag", overwrite_rejected)
        collision_rejections = 0
        for collision in (manifest, output_directory / "chapter-01.mp3", chunk1):
            try:
                process_manifest(manifest, output_directory, collision, settings, True)
            except MasteringError:
                collision_rejections += 1
        check("report source manifest and output collisions rejected", collision_rejections == 3)
        protected_qa = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--qa-only", str(chunk1),
                                       "--report", str(chunk1)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      stdin=subprocess.DEVNULL, check=False)
        check("QA report cannot replace analyzed source", protected_qa.returncode == 2 and hashes_before[0] == hashlib.sha256(chunk1.read_bytes()).hexdigest())
        sibling_manifest = directory / "collision.json.tmp"
        sibling_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
        sibling_before = sibling_manifest.read_bytes()
        save_json(directory / "collision.json", {"synthetic": True})
        check("unique atomic report temporary preserves existing sibling manifest", sibling_manifest.read_bytes() == sibling_before)
        result = {"passed": True, "checks": checks, "normal_chapter": normal, "high_crest_chapter": crest,
                  "speechlike_spikes_chapter": gentle,
                  "scope": "Synthetic sine, impulse, and quiet-segment files only; no manuscript narration, network, API, keys, or external downloads. Temporary audio removed after test."}
        save_json(report, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--input-list", type=Path)
    mode.add_argument("--qa-only", nargs="+", type=Path, metavar="AUDIO")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "mastered")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace existing exported files; raw inputs are never replaced")
    parser.add_argument("--ffmpeg", default="/opt/homebrew/bin/ffmpeg")
    parser.add_argument("--ffprobe", default="/opt/homebrew/bin/ffprobe")
    parser.add_argument("--target-rms-db", type=float, default=-20.0)
    parser.add_argument("--peak-ceiling-db", type=float, default=-3.2)
    parser.add_argument("--max-gain-db", type=float, default=30.0)
    parser.add_argument("--peak-limiter", choices=("auto", "off"), default="auto", help="Gentle oversampled limiting only when static gain is peak-constrained")
    parser.add_argument("--max-limiter-overdrive-db", type=float, default=6.0, help="Maximum pre-limiter peak overdrive; default 6 dB")
    parser.add_argument("--silence-threshold-db", type=float, default=-60.0)
    parser.add_argument("--long-silence-seconds", type=float, default=2.0)
    parser.add_argument("--no-edge-trim", action="store_true", help="Retain original outer silence as well as internal cadence")
    args = parser.parse_args()
    settings = Settings(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, target_rms_db=args.target_rms_db,
                        peak_ceiling_db=args.peak_ceiling_db, max_gain_db=args.max_gain_db,
                        silence_db=args.silence_threshold_db, long_silence_seconds=args.long_silence_seconds,
                        trim_edges=not args.no_edge_trim, peak_limiter=args.peak_limiter,
                        max_limiter_overdrive_db=args.max_limiter_overdrive_db)
    if not all(math.isfinite(value) for value in (settings.target_rms_db, settings.peak_ceiling_db, settings.max_gain_db, settings.silence_db, settings.long_silence_seconds, settings.max_limiter_overdrive_db)):
        parser.error("Numeric settings must be finite")
    if settings.target_rms_db >= 0 or settings.peak_ceiling_db >= 0 or settings.long_silence_seconds <= 0 or settings.max_gain_db < 0:
        parser.error("Level ceilings must be negative; long-silence duration positive; maximum gain nonnegative")
    if not (-80 <= settings.target_rms_db <= -1 and -24 <= settings.peak_ceiling_db <= -0.1 and
            0 <= settings.max_gain_db <= 80 and -160 <= settings.silence_db <= -20):
        parser.error("Supported ranges: RMS -80..-1 dBFS, peak -24..-0.1 dBFS, gain 0..80 dB, silence -160..-20 dBFS")
    if not 0 <= settings.max_limiter_overdrive_db <= 12:
        parser.error("Limiter peak overdrive must be between 0 and 12 dB")
    if args.report:
        protected = {Path(__file__).resolve()}
        if args.input_list:
            protected.add(args.input_list.resolve())
        if args.qa_only:
            protected.update(path.resolve() for path in args.qa_only)
        if args.report.resolve() in protected:
            parser.error("Report would overwrite a source audio file, manifest, or helper")
        if args.report.suffix.lower() != ".json":
            parser.error("Report destination must have a .json extension")
    try:
        if args.self_test:
            report = args.report or Path(__file__).resolve().parent / "master_audio-self-test.json"
            result = self_test(settings, report)
            print(f"Self-test passed: {len(result['checks'])} checks. Report: {report}")
        elif args.qa_only:
            result = {"files": [{"path": str(path.resolve()), "qa": analyze_audio(path.resolve(), settings)} for path in args.qa_only]}
            if args.report:
                save_json(args.report, result)
            else:
                print(json.dumps(result, indent=2, allow_nan=False))
        else:
            report = args.report or args.output_dir / "mastering-report.json"
            process_manifest(args.input_list.resolve(), args.output_dir.resolve(), report.resolve(), settings, args.overwrite)
            print(f"Technical mastering complete. Report: {report}")
    except (MasteringError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
