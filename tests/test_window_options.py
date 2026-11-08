"""Meaningful checks for preselected windows and the cross-episode gate."""

import json
import tempfile
import unittest
from pathlib import Path

from fixtures import screenplay_text
from llm_client import load_config
from run_stage3 import run
from screenplay import load_show
from test_pipeline import FakeClient
from validators import validate_candidate
from window_options import make_local_windows, resolve_local_choice


class WindowOptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input_dir = self.root / "input"
        self.input_dir.mkdir()
        for number in (1, 2, 3):
            (self.input_dir / f"episode-{number:03d}.txt").write_text(screenplay_text(number), encoding="utf-8")
        self.episodes, _ = load_show(self.input_dir)
        self.config = load_config("config.local.json")
        self.config.update({"cache_dir": str(self.root / "cache"), "raw_outputs_dir": str(self.root / "raw")})

    def test_windows_have_verified_length_boundaries_and_hook_types(self):
        windows = make_local_windows(self.episodes[1])
        self.assertTrue(windows)
        self.assertEqual({item["hook_type"] for item in windows}, {"dialogue", "action"})
        for item in windows:
            event = next(event for event in self.episodes[1]["events"] if event["event_id"] == item["hook_event_id"])
            candidate = {
                "segments": item["segments"],
                "hook": {"episode": 1, "timestamp": event["start"], "quote": event["text"]},
                "cliffhanger": "Review this.", "why_it_works": "Review this.", "context_needed": "Review this.",
            }
            self.assertTrue(validate_candidate(candidate, self.episodes)["valid"])
            self.assertGreaterEqual(item["duration_seconds"], 20)
            self.assertLessEqual(item["duration_seconds"], 60)

    def test_unknown_or_conflicting_window_is_rejected(self):
        windows = make_local_windows(self.episodes[1])
        candidate, error = resolve_local_choice({"window_id": "invented"}, windows)
        self.assertIsNone(candidate)
        self.assertIn("does not match", error)
        candidate, error = resolve_local_choice({"window_id": windows[0]["window_id"], "segments": []}, windows)
        self.assertIsNone(candidate)
        self.assertIn("conflict", error)
        candidate, error = resolve_local_choice({"window_id": windows[0]["window_id"], "hook_event_id": "invented"}, windows)
        self.assertIsNone(candidate)
        self.assertIn("conflict", error)
        candidate, error = resolve_local_choice({"window_id": []}, windows)
        self.assertIsNone(candidate)
        self.assertIn("does not match", error)

    def test_no_cross_request_when_only_one_episode_has_a_valid_window(self):
        class OneEpisodeClient(FakeClient):
            def ask_json(self, prompt, required_list):
                if "\nEPISODE DATA:\n" in prompt:
                    episode_data = json.loads(prompt.split("\nEPISODE DATA:\n", 1)[1])
                    if episode_data["episode"] != 1:
                        self.calls.append(required_list)
                        return {"candidates": []}, []
                if "\nSELECTED EPISODES AND SOURCE EVENTS:\n" in prompt:
                    raise AssertionError("Cross pass should not run with only one episode")
                return super().ask_json(prompt, required_list)
        report = run(str(self.input_dir), [1, 2, 3], self.config, client=OneEpisodeClient())
        self.assertEqual(len(report["candidates"]), 1)
        self.assertIn("fewer than two", report["cross_episode_status"])


if __name__ == "__main__":
    unittest.main()
