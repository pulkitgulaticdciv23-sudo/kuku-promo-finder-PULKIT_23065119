"""Deterministic window stitching, scoring, deduplication and diversity."""

from __future__ import annotations

import hashlib
import json

from screenplay import MICROSECONDS, time_to_us
from validators import cut_points, validate_candidate


SCORE_KEYS = ("hook_strength", "cliffhanger_strength", "standalone_clarity",
              "emotional_intensity", "spoiler_risk", "genre_fit")
WEIGHT_KEYS = ("hook_strength", "cliffhanger_strength", "standalone_clarity",
               "emotional_intensity", "genre_fit", "spoiler_avoidance",
               "speech_opening", "dialogue_density", "length_sweet_spot", "early_episode")


def stable_key(candidate: dict) -> str:
    """Same source segments get the same fingerprint across independent runs."""
    data = [(s["episode"], time_to_us(s["start"]), time_to_us(s["end"])) for s in candidate["segments"]]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()[:16]


def exact_self_scores(value: object) -> dict | None:
    """Ignore missing or malformed self-scores; never invent them."""
    if not isinstance(value, dict):
        return None
    if any(type(value.get(key)) is not int or not 1 <= value[key] <= 10 for key in SCORE_KEYS):
        return None
    return {key: value[key] for key in SCORE_KEYS}


def source_events(episode: dict, segment: dict, include_overlaps: bool = False) -> list[dict]:
    start, end = time_to_us(segment["start"]), time_to_us(segment["end"])
    if include_overlaps:
        return [e for e in episode["events"] if e["start_us"] < end and e["end_us"] > start]
    return [e for e in episode["events"] if e["start_us"] >= start and e["end_us"] <= end]


def _union_length(intervals: list[tuple[int, int]]) -> int:
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def overlap_iou(left: dict, right: dict) -> float:
    """Intersection/union of actual episode-relative intervals, not timestamps alone."""
    a = [(s["episode"], time_to_us(s["start"]), time_to_us(s["end"])) for s in left["segments"]]
    b = [(s["episode"], time_to_us(s["start"]), time_to_us(s["end"])) for s in right["segments"]]
    intersection = sum(max(0, min(a1, b1) - max(a0, b0)) for ep_a, a0, a1 in a for ep_b, b0, b1 in b if ep_a == ep_b)
    total_a = sum(_union_length([(s, e) for ep, s, e in a if ep == n]) for n in {ep for ep, _, _ in a})
    total_b = sum(_union_length([(s, e) for ep, s, e in b if ep == n]) for n in {ep for ep, _, _ in b})
    union = total_a + total_b - intersection
    return intersection / union if union else 0.0


def code_features(candidate: dict, episodes: dict[int, dict]) -> dict:
    first = candidate["segments"][0]
    first_start = time_to_us(first["start"])
    first_end = time_to_us(first["end"])
    first_events = episodes[first["episode"]]["events"]
    speech = any(e["type"] == "dialogue" and first_start <= e["start_us"] < min(first_start + 3 * MICROSECONDS, first_end)
                 and e["end_us"] <= first_end for e in first_events)
    total, spoken = 0, 0
    for segment in candidate["segments"]:
        start, end = time_to_us(segment["start"]), time_to_us(segment["end"])
        total += end - start
        spoken += _union_length([(max(start, e["start_us"]), min(end, e["end_us"]))
                                for e in episodes[segment["episode"]]["events"]
                                if e["type"] == "dialogue" and e["start_us"] < end and e["end_us"] > start])
    seconds = total / MICROSECONDS
    if 35 <= seconds <= 55:
        length = 1.0
    elif seconds < 35:
        length = max(0.0, (seconds - 20) / 15)
    else:
        length = max(0.0, (60 - seconds) / 5)
    order = sorted(episodes)
    early = 1.0 if len(order) == 1 else 1 - order.index(first["episode"]) / (len(order) - 1)
    return {"speech_opening": float(speech), "dialogue_density": min(1.0, spoken / total) if total else 0.0,
            "length_sweet_spot": length, "early_episode": early}


def score_candidate(candidate: dict, judgement: dict, episodes: dict[int, dict], weights: dict) -> dict:
    if set(weights) != set(WEIGHT_KEYS) or any(type(v) not in (int, float) or v < 0 for v in weights.values()):
        raise ValueError("scoring_weights must contain the ten documented nonnegative weights")
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError("scoring_weights must sum to 1")
    scores = judgement["scores"]
    values = {key: (scores[key]["score"] - 1) / 9 for key in
              ("hook_strength", "cliffhanger_strength", "standalone_clarity", "emotional_intensity", "genre_fit")}
    values["spoiler_avoidance"] = (10 - scores["spoiler_risk"]["score"]) / 9
    values.update(code_features(candidate, episodes))
    breakdown = {key: {"value": round(values[key], 4), "weight": weights[key],
                       "points": round(100 * weights[key] * values[key], 2)} for key in WEIGHT_KEYS}
    return {"base_score": round(sum(100 * weights[k] * values[k] for k in WEIGHT_KEYS), 2), "score_breakdown": breakdown}


def character_set(candidate: dict, episodes: dict[int, dict]) -> set[str]:
    return {e["speaker"] for seg in candidate["segments"] for e in source_events(episodes[seg["episode"]], seg)
            if e["type"] == "dialogue" and e.get("speaker")}


def rank_diverse(candidates: list[dict], episodes: dict[int, dict], threshold: float,
                 episode_penalty: float, block_penalty: float, character_penalty: float) -> tuple[list[dict], list[dict]]:
    """Discard near duplicates, then greedily penalize repetitive episode/character picks."""
    ordered = sorted(candidates, key=lambda x: (-x["base_score"], stable_key(x)))
    unique, removed = [], []
    for item in ordered:
        match = next((keep for keep in unique if overlap_iou(item, keep) >= threshold), None)
        if match:
            removed.append({"candidate_id": item["candidate_id"], "kept_candidate_id": match["candidate_id"],
                            "overlap_iou": round(overlap_iou(item, match), 4)})
        else:
            unique.append(item)
    episode_order = {n: i for i, n in enumerate(sorted(episodes))}
    characters = {item["candidate_id"]: character_set(item, episodes) for item in unique}
    selected = []
    while unique:
        def evaluate(item):
            number = item["segments"][0]["episode"]
            block = episode_order[number] // 5  # A temporal proxy, not a verified story arc.
            chars = characters[item["candidate_id"]]
            repeated_ep = sum(number == prior["segments"][0]["episode"] for prior in selected)
            repeated_block = sum(block == episode_order[prior["segments"][0]["episode"]] // 5 for prior in selected)
            similarities = []
            for prior in selected:
                other = characters[prior["candidate_id"]]
                if chars and other:
                    similarities.append(len(chars & other) / len(chars | other))
            char_sim = max(similarities, default=0.0)
            penalty = episode_penalty * repeated_ep + block_penalty * repeated_block + character_penalty * char_sim
            return item["base_score"] - penalty, penalty
        best = min(unique, key=lambda x: (-evaluate(x)[0], -x["base_score"], stable_key(x)))
        adjusted, penalty = evaluate(best)
        best = dict(best, rank=len(selected) + 1, diversity_penalty=round(penalty, 2), final_score=round(max(0.0, adjusted), 2))
        selected.append(best)
        unique = [item for item in unique if item["candidate_id"] != best["candidate_id"]]
    return selected, removed


def make_cross_windows(episodes: dict[int, dict], candidates: list[dict], episode_numbers: list[int], limit: int = 12) -> list[dict]:
    """Offer short, source-backed pieces from chronological episodes to the model."""
    by_episode = {}
    for candidate in candidates:
        if len(candidate["segments"]) == 1:
            by_episode.setdefault(candidate["segments"][0]["episode"], candidate)
    numbers = [n for n in episode_numbers if n in by_episode]
    pairs = [(numbers[i], numbers[j]) for jump in (1, 2) for i in range(len(numbers))
             for j in [i + jump] if j < len(numbers)]
    options = []
    safe_cache = {n: [p for p in cut_points(episodes[n]) if p["safe"]] for n in numbers}
    for a, b in pairs:
        left, right = by_episode[a], by_episode[b]
        ls, rs = left["segments"][0], right["segments"][0]
        left_start, left_end = time_to_us(ls["start"]), time_to_us(ls["end"])
        right_start, right_end = time_to_us(rs["start"]), time_to_us(rs["end"])
        hook = next((e for e in episodes[a]["events"] if e["event_id"] == left["hook_event_id"]), None)
        if hook is None:
            continue
        ends = [p for p in safe_cache[a] if 10 * MICROSECONDS <= p["time_us"] - left_start <= 24 * MICROSECONDS
                and p["time_us"] <= left_end and p["time_us"] >= hook["end_us"]]
        starts = [p for p in safe_cache[b] if 10 * MICROSECONDS <= right_end - p["time_us"] <= 24 * MICROSECONDS
                  and p["time_us"] >= right_start]
        if not ends or not starts:
            continue
        le = min(ends, key=lambda p: abs(p["time_us"] - (left_start + 17 * MICROSECONDS)))
        rs_point = min(starts, key=lambda p: abs(p["time_us"] - (right_end - 17 * MICROSECONDS)))
        segments = [{"episode": a, "start": ls["start"], "end": le["source_timestamps"][0]},
                    {"episode": b, "start": rs_point["source_timestamps"][0], "end": rs["end"]}]
        trial = {"segments": segments, "hook": left["hook"], "cliffhanger": "Review needed.",
                 "why_it_works": "Review needed.", "context_needed": "Review needed."}
        verdict = validate_candidate(trial, episodes)
        if verdict["valid"]:
            options.append({"window_id": f"X{a:03d}-{b:03d}-{len(options)+1:02d}", "segments": segments,
                            "duration_seconds": verdict["duration_seconds"], "hook_event_id": left["hook_event_id"],
                            "hook_text": left["hook"]["quote"]})
        if len(options) >= limit:
            break
    return options
