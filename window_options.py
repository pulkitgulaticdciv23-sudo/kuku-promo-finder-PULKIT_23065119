"""Build a small menu of source-verified continuous promo windows.

The model chooses among these options; code does not silently extend a model's
proposed clip. This bounded menu trades coverage for reliability and keeps the
number of alternatives manageable for a small local model.
"""

from __future__ import annotations

import re

from validators import cut_points, validate_candidate


def _evenly_spaced(items: list, count: int) -> list:
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    return [items[round(i * (len(items) - 1) / (count - 1))] for i in range(count)]


def make_local_windows(episode: dict, max_hooks: int = 12) -> list[dict]:
    """Return candidate windows that pass the existing structural validator."""
    if type(max_hooks) is not int or max_hooks < 1:
        raise ValueError("max_hooks must be a positive integer")
    episode_number = episode["episode_number"]
    safe = [point for point in cut_points(episode) if point["safe"]]
    safe_us = {point["time_us"] for point in safe}
    dialogue_ends = {event["end_us"] for event in episode["events"] if event["type"] == "dialogue"}

    eligible = []
    for hook in episode["events"]:
        if hook["type"] not in ("dialogue", "action") or hook["start_us"] not in safe_us:
            continue
        possible = [point for point in safe
                    if 20_000_000 <= point["time_us"] - hook["start_us"] <= 60_000_000
                    and point["time_us"] >= hook["end_us"]]
        if possible:
            eligible.append((hook, possible))
    eligible.sort(key=lambda item: (item[0]["start_us"], item[0]["event_id"]))
    actions = [item for item in eligible if item[0]["type"] == "action"]
    dialogues = [item for item in eligible if item[0]["type"] == "dialogue"]
    action_quota = min(len(actions), max(1, max_hooks // 3)) if actions else 0
    # These simple words are a recall aid, not a judgment that an action is good.
    action_words = re.compile(r"\b(slap|slaps|punch|punches|strik\w*|struck|hit|hits|attack\w*|grab\w*|kick\w*|block\w*|shoot\w*|gun|knife|blood|fight\w*)\b", re.I)
    forceful = [item for item in actions if action_words.search(item[0]["text"])]
    other_actions = [item for item in actions if item not in forceful]
    chosen_actions = _evenly_spaced(forceful, action_quota)
    chosen_actions += _evenly_spaced(other_actions, action_quota - len(chosen_actions))
    chosen_dialogues = _evenly_spaced(dialogues, max_hooks - len(chosen_actions))
    spare = max_hooks - len(chosen_actions) - len(chosen_dialogues)
    if spare:
        chosen_actions += _evenly_spaced([item for item in actions if item not in chosen_actions], spare)
    eligible = sorted(chosen_actions + chosen_dialogues, key=lambda item: (item[0]["start_us"], item[0]["event_id"]))

    windows = []
    seen = set()
    for hook, possible in eligible:
        # Prefer ending just after dialogue, while preserving a legal boundary.
        dialogue_points = [point for point in possible if point["time_us"] in dialogue_ends]
        ends = dialogue_points or possible
        for target in (30_000_000, 45_000_000, 55_000_000):
            end = min(ends, key=lambda point: (abs(point["time_us"] - hook["start_us"] - target), point["time_us"]))
            identity = (hook["event_id"], end["time_us"])
            if identity in seen:
                continue
            seen.add(identity)
            segment = {"episode": episode_number, "start": hook["start"], "end": end["source_timestamps"][0]}
            candidate = {
                "segments": [segment],
                "hook": {"episode": episode_number, "timestamp": hook["start"], "quote": hook["text"]},
                "cliffhanger": "Requires editorial review.",
                "why_it_works": "Requires editorial review.",
                "context_needed": "Requires editorial review.",
            }
            check = validate_candidate(candidate, {episode_number: episode})
            if not check["valid"]:
                continue
            lines = [event for event in episode["events"]
                     if event["type"] == "dialogue" and event["start_us"] >= hook["start_us"]
                     and event["end_us"] <= end["time_us"]]
            last = max(lines, key=lambda event: event["end_us"]) if lines else None
            windows.append({
                "window_id": f"E{episode_number:03d}-W{len(windows) + 1:03d}",
                "segments": [segment],
                "duration_seconds": check["duration_seconds"],
                "hook_event_id": hook["event_id"],
                "hook_type": hook["type"],
                "hook_text": hook["text"],
                "last_dialogue": last["text"] if last else None,
            })
    return windows


def resolve_local_choice(raw: object, windows: list[dict]) -> tuple[dict | None, str | None]:
    """Resolve a selected ID to its unmodified source window; reject contradictions."""
    if not isinstance(raw, dict):
        return None, "window choice must be an object"
    window = next((item for item in windows if item["window_id"] == raw.get("window_id")), None)
    if window is None:
        return None, "window_id does not match an offered source window"
    if "segments" in raw and raw["segments"] != window["segments"]:
        return None, "model supplied segments that conflict with its window_id"
    if "hook_event_id" in raw and raw["hook_event_id"] != window["hook_event_id"]:
        return None, "model supplied a hook that conflicts with its window_id"
    return {
        "segments": window["segments"],
        "hook_event_id": window["hook_event_id"],
        "cliffhanger": raw.get("cliffhanger"),
        "why_it_works": raw.get("why_it_works"),
        "context_needed": raw.get("context_needed"),
    }, None
