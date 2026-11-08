"""Read screenplay folders or ZIP files without extracting their contents."""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath


# Integers keep time comparisons exact. One second is one million microseconds.
MICROSECONDS = 1_000_000
MAX_FILE_BYTES = 5 * 1024 * 1024
TIME_RE = re.compile(r"^(\d+):([0-5]\d)(?:\.(\d{1,6}))?$")
SCENE_RE = re.compile(r"^SCENE\s+(\d+)\s*$", re.IGNORECASE)
SCENE_FIELD_RE = re.compile(r"^SCENE\s+(HEADING|START|END)\s*:\s*(.*)$", re.IGNORECASE)
BLOCK_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIMING_RE = re.compile(
    r"^START\s*:\s*\(([^)]*)\)\s*\|\s*END\s*:\s*\(([^)]*)\)\s*$",
    re.IGNORECASE,
)
FILENAME_EPISODE_RE = re.compile(r"episode[-_ ]+(\d+)", re.IGNORECASE)


def time_to_us(value: str) -> int:
    """Convert MM:SS[.fraction] to an integer; reject invalid or rounded times."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid timestamp: {value!r}")
    minutes, seconds, fraction = match.groups()
    fraction = (fraction or "").ljust(6, "0")
    return (int(minutes) * 60 + int(seconds)) * MICROSECONDS + int(fraction)


def us_to_timestamp(value: int) -> str:
    """Format a duration for display; source cut points keep their original spelling."""
    if value < 0:
        raise ValueError("time cannot be negative")
    whole, fraction = divmod(value, MICROSECONDS)
    minutes, seconds = divmod(whole, 60)
    digits = f"{fraction:06d}".rstrip("0")
    digits = digits.ljust(2, "0")
    return f"{minutes:02d}:{seconds:02d}.{digits}"


def _warning(warnings: list[str], source: str, line: int | None, message: str) -> None:
    location = f"{source}:{line}" if line is not None else source
    warnings.append(f"{location}: {message}")


def _speaker_parts(label: str) -> tuple[str, str | None, list[str]]:
    """Preserve the label separately because a parenthetical can be ambiguous."""
    first_parenthesis = label.find("(")
    if first_parenthesis < 0:
        return label.strip(), None, []
    speaker = label[:first_parenthesis].strip()
    parts = re.findall(r"\(([^()]*)\)", label[first_parenthesis:])
    if not parts:
        return label.strip(), None, []
    return speaker, parts[-1].strip(), [part.strip() for part in parts[:-1]]


def _parse_episode(text: str, source: str, warnings: list[str]) -> dict | None:
    lines = text.splitlines()
    metadata: dict[str, str] = {}
    names = {"EPISODE NUMBER", "EPISODE LOGLINE", "CONTINUITY NOTES"}
    script_start = None
    script_end = None

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if stripped.upper() == "--- SCRIPT START ---":
            script_start = index
            break
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip().upper()
            if key in names:
                if key in metadata:
                    _warning(warnings, source, index + 1, f"duplicate {key}; keeping first")
                else:
                    metadata[key] = value.strip()

    if script_start is None:
        _warning(warnings, source, None, "missing SCRIPT START; attempting to read scenes")
        script_start = -1
    for index in range(script_start + 1, len(lines)):
        if lines[index].strip().upper() == "--- SCRIPT END ---":
            script_end = index
            break
    if script_end is None:
        _warning(warnings, source, None, "missing SCRIPT END; reading to end of file")
        script_end = len(lines)

    number_match = re.search(r"\b(\d+)\b", metadata.get("EPISODE NUMBER", ""))
    filename_match = FILENAME_EPISODE_RE.search(PurePosixPath(source).name)
    if number_match:
        number = int(number_match.group(1))
        if filename_match and int(filename_match.group(1)) != number:
            _warning(warnings, source, None, "episode number differs from filename; using header")
    elif filename_match:
        number = int(filename_match.group(1))
        _warning(warnings, source, None, f"missing episode number; using filename number {number}")
    else:
        _warning(warnings, source, None, "missing episode number; skipping file")
        return None
    for key in ("EPISODE LOGLINE", "CONTINUITY NOTES"):
        if not metadata.get(key):
            _warning(warnings, source, None, f"missing {key}")

    episode = {
        "episode_number": number,
        "source_filename": PurePosixPath(source).name,
        "episode_logline": metadata.get("EPISODE LOGLINE", ""),
        "continuity_notes": metadata.get("CONTINUITY NOTES", ""),
        "scenes": [],
        "events": [],
    }
    current_scene: dict | None = None
    seen_scene_ids: set[int] = set()
    cursor = script_start + 1
    event_number = 0
    while cursor < script_end:
        line = lines[cursor].strip()
        if not line or line.upper() == "ACTIONS AND DIALOGUE:":
            cursor += 1
            continue
        scene_match = SCENE_RE.fullmatch(line)
        if scene_match:
            scene_id = int(scene_match.group(1))
            if scene_id in seen_scene_ids:
                _warning(warnings, source, cursor + 1, f"duplicate scene number {scene_id}")
            seen_scene_ids.add(scene_id)
            current_scene = {"scene_id": scene_id, "heading": "", "start": None, "end": None}
            episode["scenes"].append(current_scene)
            cursor += 1
            continue
        field_match = SCENE_FIELD_RE.fullmatch(line)
        if field_match:
            if current_scene is None:
                _warning(warnings, source, cursor + 1, "scene field without a scene; skipping")
            else:
                field = field_match.group(1).lower()
                value = field_match.group(2).strip()
                if field in ("start", "end"):
                    try:
                        time_to_us(value)
                    except ValueError:
                        _warning(warnings, source, cursor + 1, f"bad scene {field} timestamp {value!r}")
                        value = None
                current_scene[field] = value
            cursor += 1
            continue
        block_match = BLOCK_RE.fullmatch(line)
        if block_match:
            label = block_match.group(1).strip()
            block_line = cursor + 1
            cursor += 1
            while cursor < script_end and not lines[cursor].strip():
                cursor += 1
            timing = TIMING_RE.fullmatch(lines[cursor].strip()) if cursor < script_end else None
            if timing:
                start, end = (part.strip() for part in timing.groups())
                cursor += 1
            else:
                start = end = None
                _warning(warnings, source, block_line, f"missing or malformed timing for {label}; skipped block")
            body: list[str] = []
            while cursor < script_end:
                next_line = lines[cursor].strip()
                if BLOCK_RE.fullmatch(next_line) or SCENE_RE.fullmatch(next_line):
                    break
                body.append(lines[cursor])
                cursor += 1
            event_text = "\n".join(body).strip()
            if timing is None:
                continue
            try:
                start_us, end_us = time_to_us(start), time_to_us(end)
                if end_us <= start_us:
                    raise ValueError("end must be after start")
            except ValueError as exc:
                _warning(warnings, source, block_line, f"bad timing for {label}: {exc}; skipped block")
                continue
            if not event_text or current_scene is None:
                _warning(warnings, source, block_line, f"missing text or scene for {label}; skipped block")
                continue
            normalized = label.upper()
            special = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sound_fx"}
            if normalized in special:
                event_type, speaker, emotion, qualifiers = special[normalized], None, None, []
            else:
                event_type = "dialogue"
                speaker, emotion, qualifiers = _speaker_parts(label)
            event_number += 1
            episode["events"].append({
                "event_id": f"E{number:03d}-{event_number:04d}",
                "type": event_type,
                "speaker": speaker,
                "emotion": emotion,
                "speaker_qualifiers": qualifiers,
                "start": start,
                "end": end,
                "start_us": start_us,
                "end_us": end_us,
                "text": event_text,
                "scene_id": current_scene["scene_id"],
                "original_label": label,
                "source_line": block_line,
            })
            continue
        _warning(warnings, source, cursor + 1, f"unrecognized line; skipped: {line[:100]!r}")
        cursor += 1

    for scene in episode["scenes"]:
        if not scene["heading"] or scene["start"] is None or scene["end"] is None:
            _warning(warnings, source, None, f"scene {scene['scene_id']} has missing or invalid fields")
            continue
        scene_start, scene_end = time_to_us(scene["start"]), time_to_us(scene["end"])
        if scene_end <= scene_start:
            _warning(warnings, source, None, f"scene {scene['scene_id']} ends before it starts")
        for event in episode["events"]:
            if event["scene_id"] == scene["scene_id"] and (
                event["start_us"] < scene_start or event["end_us"] > scene_end
            ):
                _warning(
                    warnings, source, event["source_line"],
                    f"{event['event_id']} extends beyond scene {scene['scene_id']}; source preserved",
                )
    # Source order is retained as a tie-breaker; some supplied blocks are out of time order.
    episode["events"].sort(key=lambda item: (item["start_us"], item["source_line"]))
    if not episode["scenes"]:
        _warning(warnings, source, None, "no scenes found")
    if not episode["events"]:
        _warning(warnings, source, None, "no timed events found")
    return episode


def load_show(path: str | Path) -> tuple[dict[int, dict], list[str]]:
    """Return episodes and human-readable warnings from a folder or ZIP file."""
    path = Path(path)
    warnings: list[str] = []
    documents: list[tuple[str, bytes]] = []
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file() and item.suffix.lower() == ".txt" and not item.name.startswith("._"):
                try:
                    if item.stat().st_size > MAX_FILE_BYTES:
                        _warning(warnings, str(item), None, "file exceeds 5 MiB; skipped")
                        continue
                    documents.append((item.relative_to(path).as_posix(), item.read_bytes()))
                except OSError as exc:
                    _warning(warnings, str(item), None, f"could not read file: {exc}")
    elif path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda entry: entry.filename):
                name = info.filename
                pure_name = PurePosixPath(name)
                if info.is_dir() or pure_name.suffix.lower() != ".txt":
                    continue
                if "__MACOSX" in pure_name.parts or pure_name.name.startswith("._"):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    _warning(warnings, name, None, "file exceeds 5 MiB; skipped")
                    continue
                try:
                    documents.append((name, archive.read(info)))
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    _warning(warnings, name, None, f"could not read ZIP member: {exc}")
    else:
        raise ValueError("input must be an existing folder or ZIP file")

    if not documents:
        _warning(warnings, str(path), None, "no readable .txt files found")
    episodes: dict[int, dict] = {}
    for source, data in documents:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            _warning(warnings, source, None, "not valid UTF-8; skipped")
            continue
        episode = _parse_episode(text, source, warnings)
        if episode is None:
            continue
        number = episode["episode_number"]
        if number in episodes:
            _warning(warnings, source, None, f"duplicate episode {number}; skipped")
            continue
        episodes[number] = episode
    return dict(sorted(episodes.items())), warnings


def show_counts(episodes: dict[int, dict]) -> dict:
    counts = Counter(event["type"] for ep in episodes.values() for event in ep["events"])
    return {
        "episodes": len(episodes),
        "scenes": sum(len(ep["scenes"]) for ep in episodes.values()),
        "events": sum(counts.values()),
        "event_types": dict(sorted(counts.items())),
    }
