"""Deterministic timestamp, segment, hook-evidence, and source-quote checks."""

from __future__ import annotations

import re

from screenplay import MICROSECONDS, time_to_us


MIN_DURATION_US = 20 * MICROSECONDS
MAX_DURATION_US = 60 * MICROSECONDS
MAX_SEGMENTS = 4
CONTINUATION_GAP_US = 2 * MICROSECONDS


def _unquote(value: str) -> str:
    """Allow surrounding quotation marks but never change the words or punctuation."""
    value = value.strip()
    for left, right in (("\"", "\""), ("“", "”"), ("'", "'"), ("‘", "’")):
        if len(value) >= 2 and value.startswith(left) and value.endswith(right):
            return value[len(left):-len(right)]
    return value


def _continuation_gaps(episode: dict) -> list[tuple[int, int]]:
    """Conservatively flag obvious same-speaker ellipsis continuations."""
    dialogue = sorted(
        (event for event in episode["events"] if event["type"] == "dialogue"),
        key=lambda event: (event["start_us"], event["source_line"]),
    )
    gaps = []
    for earlier, later in zip(dialogue, dialogue[1:]):
        gap = later["start_us"] - earlier["end_us"]
        same_speaker = (earlier["speaker"] or "").casefold() == (later["speaker"] or "").casefold()
        later_text = _unquote(later["text"]).lstrip()
        if same_speaker and 0 <= gap <= CONTINUATION_GAP_US and later_text.startswith(("...", "…")):
            gaps.append((earlier["end_us"], later["start_us"]))
    return gaps


def _unsafe_reasons(episode: dict, position: int) -> list[str]:
    reasons = []
    for event in episode["events"]:
        if event["type"] == "dialogue" and event["start_us"] < position < event["end_us"]:
            reasons.append(f"inside dialogue event {event['event_id']}")
    if any(start <= position <= end for start, end in _continuation_gaps(episode)):
        reasons.append("splits an apparent same-speaker ellipsis continuation")
    return reasons


def cut_points(episode: dict) -> list[dict]:
    """List real event boundaries and why each is usable or unsafe."""
    spellings: dict[int, set[str]] = {}
    for event in episode["events"]:
        for field in ("start", "end"):
            value = event[field + "_us"]
            spellings.setdefault(value, set()).add(event[field])
    points = []
    for value in sorted(spellings):
        reasons = _unsafe_reasons(episode, value)
        points.append({
            "time_us": value,
            "source_timestamps": sorted(spellings[value]),
            "safe": not reasons,
            "reasons": reasons,
        })
    return points


def snap_timestamp(
    episode: dict, timestamp: str, direction: str = "nearest", max_shift_seconds: float = 2.0
) -> str:
    """Return a source boundary close to the input; never create a new timestamp."""
    if direction not in {"nearest", "earlier", "later"}:
        raise ValueError("direction must be nearest, earlier, or later")
    if not isinstance(max_shift_seconds, (int, float)) or max_shift_seconds < 0:
        raise ValueError("max_shift_seconds must be nonnegative")
    requested = time_to_us(timestamp)
    points = [point for point in cut_points(episode) if point["safe"]]
    if direction == "earlier":
        points = [point for point in points if point["time_us"] <= requested]
    if direction == "later":
        points = [point for point in points if point["time_us"] >= requested]
    if not points:
        raise ValueError("no safe cut point in the requested direction")
    chosen = min(points, key=lambda point: (abs(point["time_us"] - requested), point["time_us"]))
    if abs(chosen["time_us"] - requested) > max_shift_seconds * MICROSECONDS:
        raise ValueError("nearest safe source boundary exceeds maximum allowed shift")
    if timestamp in chosen["source_timestamps"]:
        return timestamp
    return chosen["source_timestamps"][0]


def _source_contains_timestamp(episode: dict, timestamp: str) -> bool:
    return any(timestamp == event[field] for event in episode["events"] for field in ("start", "end"))


def events_after_cut(episode: dict, timestamp: str, window_seconds: int = 5) -> list[dict]:
    """Return source events overlapping the next few seconds for editorial review."""
    if type(window_seconds) is not int or window_seconds <= 0:
        raise ValueError("window_seconds must be a positive integer")
    position = time_to_us(timestamp)
    if not _source_contains_timestamp(episode, timestamp):
        raise ValueError("cut timestamp is not an exact source event boundary")
    end = position + window_seconds * MICROSECONDS
    return [event for event in episode["events"] if event["start_us"] < end and event["end_us"] > position]


def _quoted_spans(value: str) -> list[str]:
    pattern = r'"([^"\n]+)"|“([^”\n]+)”|(?<!\w)\x27([^\x27\n]+)\x27(?!\w)|‘([^’\n]+)’'
    return [next(part for part in match.groups() if part is not None) for match in re.finditer(pattern, value)]


def _narrative_strings(value: object, path: tuple = ()):
    """Walk optional candidate fields without rechecking structured timing fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _narrative_strings(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _narrative_strings(item, path + (index,))
    elif isinstance(value, str):
        if len(path) == 3 and path[0] == "segments" and path[2] in ("start", "end"):
            return
        if path in (("hook", "timestamp"), ("hook", "quote")):
            return
        yield path, value


def validate_candidate(candidate: dict, episodes: dict[int, dict]) -> dict:
    """Check a candidate; return detailed errors instead of stopping a batch run."""
    errors: list[str] = []
    warnings: list[str] = []
    valid_segments: list[tuple[int, int, int]] = []
    total_us = 0
    if not isinstance(candidate, dict):
        return {"valid": False, "errors": ["candidate must be an object"], "warnings": [], "duration_seconds": None}
    segments = candidate.get("segments")
    if not isinstance(segments, list) or not segments:
        return {"valid": False, "errors": ["segments must be a nonempty list"], "warnings": [], "duration_seconds": None}
    if len(segments) > MAX_SEGMENTS:
        errors.append(f"at most {MAX_SEGMENTS} segments are allowed")
    for field in ("cliffhanger", "why_it_works", "context_needed"):
        if not isinstance(candidate.get(field), str) or not candidate[field].strip():
            errors.append(f"{field} must be a nonempty explanation")

    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            errors.append(f"segment {index}: must be an object")
            continue
        number = segment.get("episode")
        if type(number) is not int or number not in episodes:
            errors.append(f"segment {index}: unknown or invalid episode {number!r}")
            continue
        episode = episodes[number]
        start_raw, end_raw = segment.get("start"), segment.get("end")
        try:
            start, end = time_to_us(start_raw), time_to_us(end_raw)
        except ValueError as exc:
            errors.append(f"segment {index}: {exc}")
            continue
        if end <= start:
            errors.append(f"segment {index}: end must be after start")
            continue
        total_us += end - start
        valid_segments.append((number, start, end))
        for label, raw, value in (("start", start_raw, start), ("end", end_raw, end)):
            if not _source_contains_timestamp(episode, raw):
                errors.append(f"segment {index}: {label} {raw!r} is not an exact source event boundary")
            else:
                for reason in _unsafe_reasons(episode, value):
                    errors.append(f"segment {index}: {label} {raw!r} {reason}")

    if not MIN_DURATION_US <= total_us <= MAX_DURATION_US:
        errors.append("total segment duration must be between 20 and 60 seconds inclusive")
    for previous, current in zip(valid_segments, valid_segments[1:]):
        prev_ep, prev_start, prev_end = previous
        next_ep, next_start, _ = current
        if next_ep < prev_ep or (next_ep == prev_ep and next_start < prev_start):
            errors.append("segments must be in episode and timestamp order")
        if next_ep == prev_ep and next_start < prev_end:
            errors.append("segments in the same episode must not overlap")

    hook = candidate.get("hook")
    if not isinstance(hook, dict):
        errors.append("hook must contain episode, timestamp, and exact source quote")
    elif not valid_segments or len(valid_segments) != len(segments):
        errors.append("hook cannot be checked until all segment locations are valid")
    else:
        first_ep, first_start, first_end = valid_segments[0]
        first_episode = episodes[first_ep]
        hook_ep, hook_stamp, hook_quote = hook.get("episode"), hook.get("timestamp"), hook.get("quote")
        if type(hook_ep) is not int or hook_ep != first_ep:
            errors.append("hook episode must match the first segment")
        if not isinstance(hook_quote, str) or not hook_quote.strip():
            errors.append("hook quote must be nonempty source text")
        try:
            hook_time = time_to_us(hook_stamp)
        except ValueError as exc:
            errors.append(f"hook: {exc}")
            hook_time = None
        if hook_time is not None:
            if not first_start <= hook_time < min(first_start + 3 * MICROSECONDS, first_end):
                errors.append("hook must start within the first 3 seconds of the first segment")
            matches = [event for event in first_episode["events"] if (
                event["type"] in ("dialogue", "action")
                and event["start"] == hook_stamp
                and event["start_us"] >= first_start and event["end_us"] <= first_end
                and isinstance(hook_quote, str) and _unquote(event["text"]) == _unquote(hook_quote)
            )]
            if not matches:
                errors.append("hook quote and timestamp do not match the same complete source dialogue/action event")
            elif all(event["type"] == "action" for event in matches):
                warnings.append("action hook is source-verified; a human or judge must assess its strength")

    # Optional explanation fields may quote an exact line or a contiguous excerpt
    # from one selected source event. The hook remains an exact complete event.
    # Prose may not hide unqualified timestamps.
    source_lines = set()
    for number, start, end in valid_segments:
        for event in episodes[number]["events"]:
            if event["start_us"] >= start and event["end_us"] <= end:
                source_lines.add(_unquote(event["text"]))
    timestamp_pattern = re.compile(r"(?<!\d)\d{1,3}:[0-5]\d(?:\.\d{1,6})?(?!\d)")
    for path, value in _narrative_strings(candidate):
        if timestamp_pattern.search(value):
            errors.append(f"{'.'.join(map(str, path))}: put timestamps in structured segment/hook fields")
        for quote in _quoted_spans(value):
            if not any(quote in source for source in source_lines):
                errors.append(f"{'.'.join(map(str, path))}: quoted text does not match selected source text")

    warnings.append("hook strength, cliffhanger, standalone clarity, and spoilers require editorial judgment")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "duration_seconds": total_us / MICROSECONDS,
    }


def validate_or_raise(candidate: dict, episodes: dict[int, dict]) -> dict:
    """Fail loudly before export; batch pipelines can use validate_candidate instead."""
    result = validate_candidate(candidate, episodes)
    if not result["valid"]:
        raise ValueError("candidate failed validation: " + "; ".join(result["errors"]))
    return result
