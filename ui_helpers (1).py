"""Helpers for the local Streamlit interface; no Streamlit dependency required."""

from __future__ import annotations

import hashlib
import io
import json
import csv
import zipfile
from pathlib import Path, PurePosixPath

from llm_client import atomic_write
from screenplay import time_to_us
from stage4_logic import source_events


MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_FILES = 1000


def store_upload(data: bytes, directory: Path) -> tuple[Path, str]:
    """Validate ZIP limits and save without extracting it or trusting its filename."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("Upload a nonempty ZIP of screenplay text files")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("ZIP exceeds the 40 MiB upload limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(files) > MAX_FILES:
                raise ValueError("ZIP contains more than 1000 files")
            if sum(entry.file_size for entry in files) > MAX_UNPACKED_BYTES:
                raise ValueError("ZIP contains more than 100 MiB of uncompressed data")
            if any(entry.flag_bits & 1 for entry in files):
                raise ValueError("Password-protected ZIPs are not supported")
            if not any(PurePosixPath(entry.filename).suffix.casefold() == ".txt" for entry in files):
                raise ValueError("ZIP contains no .txt screenplay files")
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a valid ZIP") from exc
    digest = hashlib.sha256(data).hexdigest()
    path = directory / f"{digest}.zip"
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        atomic_write(path, data)
    return path, digest


def ui_run_label(upload_hash: str, config: dict, variant: str) -> str:
    if variant not in ("A", "B"):
        raise ValueError("Run variant must be A or B")
    # Cache and output directories do not change the model or its scoring.
    settings = {key: value for key, value in config.items() if key not in ("cache_dir", "raw_outputs_dir")}
    config_hash = hashlib.sha256(json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:6]
    return f"UI_{upload_hash[:8]}_{config_hash}_{variant}"


def table_rows(ranked: list[dict]) -> list[dict]:
    return [{"Rank": c["rank"], "Episode(s)": ", ".join(str(s["episode"]) for s in c["segments"]),
             "Segments": "; ".join(f"E{s['episode']} {s['start']}–{s['end']}" for s in c["segments"]),
             "Seconds": c["duration_seconds"], "Hook": c["hook"]["quote"],
             "Cliffhanger": c["cliffhanger"], "Score": c["final_score"]} for c in ranked]


def script_lines(candidate: dict, episodes: dict[int, dict]) -> list[str]:
    lines = []
    for number, seg in enumerate(candidate["segments"], 1):
        ep = episodes.get(seg["episode"])
        if ep is None:
            lines.append(f"Segment {number}: episode {seg['episode']} not found in uploaded ZIP")
            continue
        start, end = time_to_us(seg["start"]), time_to_us(seg["end"])
        lines.append(f"SEGMENT {number} — EPISODE {seg['episode']} — {seg['start']}–{seg['end']}")
        for event in source_events(ep, seg, include_overlaps=True):
            label = "INSIDE" if event["start_us"] >= start and event["end_us"] <= end else "OVERLAPS CUT"
            speaker = f" ({event['speaker']})" if event.get("speaker") else ""
            lines.append(f"[{label}] {event['start']}–{event['end']} {event['type']}{speaker}: {event['text']}")
        lines.append("")
    return lines


def review_path(directory: Path, show_fingerprint: str, run_label: str) -> Path:
    """Keep user ratings separate from the original, unedited model results."""
    digest = hashlib.sha256(f"{show_fingerprint}:{run_label}".encode("utf-8")).hexdigest()
    return directory / f"review_{digest}.json"


def load_reviews(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Could not read the saved review file") from exc
    if not isinstance(value, dict):
        raise ValueError("The saved review file is invalid")
    return value


def save_review(path: Path, candidate_id: str, verdict: str, notes: str) -> dict:
    if verdict not in ("Not reviewed", "Good", "Bad", "Unsure"):
        raise ValueError("Choose a valid review verdict")
    reviews = load_reviews(path)
    if verdict == "Not reviewed" and not notes.strip():
        reviews.pop(candidate_id, None)
    else:
        reviews[candidate_id] = {"verdict": verdict, "notes": notes}
    atomic_write(path, json.dumps(reviews, ensure_ascii=False, indent=2).encode("utf-8"))
    return reviews


def review_csv(ranked: list[dict], reviews: dict, limit: int = 10) -> str:
    """Export a new checklist without changing the original audit template."""
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["rank", "candidate_id", "verdict_good_or_bad", "notes"])
    writer.writeheader()
    for candidate in ranked[:limit]:
        review = reviews.get(candidate["candidate_id"], {})
        verdict = review.get("verdict", "")
        notes = str(review.get("notes", ""))
        if notes.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            notes = "'" + notes
        writer.writerow({"rank": candidate["rank"], "candidate_id": candidate["candidate_id"],
                         "verdict_good_or_bad": "" if verdict == "Not reviewed" else verdict,
                         "notes": notes})
    return out.getvalue()
