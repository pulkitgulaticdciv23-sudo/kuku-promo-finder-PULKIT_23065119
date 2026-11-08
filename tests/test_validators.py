import copy
import tempfile
import unittest

from fixtures import sample_candidate, write_show
from screenplay import load_show
from validators import cut_points, events_after_cut, snap_timestamp, validate_candidate, validate_or_raise


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.episodes, warnings = load_show(write_show(self.tmp.name))
        self.assertEqual(warnings, [])
        self.candidate = sample_candidate()

    def test_valid_candidate_and_duration(self):
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["duration_seconds"], 30.0)

    def test_required_editorial_fields_cannot_be_omitted(self):
        for field in ("cliffhanger", "why_it_works", "context_needed"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.candidate)
                del candidate[field]
                result = validate_candidate(candidate, self.episodes)
                self.assertTrue(any(field in error for error in result["errors"]))

    def test_cut_inside_dialogue_is_rejected_even_if_camera_boundary(self):
        self.candidate["segments"][0]["end"] = "00:25.00"
        result = validate_candidate(self.candidate, self.episodes)
        self.assertFalse(result["valid"])
        self.assertTrue(any("inside dialogue" in error for error in result["errors"]))

    def test_same_speaker_ellipsis_continuation_is_not_safe(self):
        by_time = {point["time_us"]: point for point in cut_points(self.episodes[1])}
        self.assertFalse(by_time[12_000_000]["safe"])
        self.assertFalse(by_time[12_200_000]["safe"])
        self.assertTrue(any("ellipsis continuation" in item for item in by_time[12_000_000]["reasons"]))

    def test_next_events_are_available_for_cliffhanger_review(self):
        events = events_after_cut(self.episodes[1], "00:20.00", window_seconds=7)
        self.assertTrue(any(event["text"] == '"Who told you?"' for event in events))
        with self.assertRaises(ValueError):
            events_after_cut(self.episodes[1], "00:20.01")

    def test_snapping_returns_a_real_safe_timestamp(self):
        self.assertEqual(snap_timestamp(self.episodes[1], "00:25.01", "later"), "00:26.00")
        self.assertEqual(snap_timestamp(self.episodes[1], "00:25.01", "earlier", 2), "00:24.00")
        with self.assertRaises(ValueError):
            snap_timestamp(self.episodes[1], "00:25.01", "later", 0.1)

    def test_invented_timestamp_is_rejected(self):
        self.candidate["segments"][0]["end"] = "00:29.99"
        result = validate_candidate(self.candidate, self.episodes)
        self.assertFalse(result["valid"])
        self.assertTrue(any("not an exact source event boundary" in error for error in result["errors"]))

    def test_numeric_equivalent_but_unwritten_timestamp_is_rejected(self):
        self.candidate["segments"][0]["end"] = "00:30.0"
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(any("not an exact source" in error for error in result["errors"]))

    def test_invented_hook_fails_loudly(self):
        self.candidate["hook"]["quote"] = "Something nobody said."
        with self.assertRaisesRegex(ValueError, "candidate failed validation"):
            validate_or_raise(self.candidate, self.episodes)

    def test_correct_quote_but_wrong_timestamp_is_rejected(self):
        self.candidate["hook"]["timestamp"] = "00:02.00"
        self.assertFalse(validate_candidate(self.candidate, self.episodes)["valid"])

    def test_hook_after_first_three_seconds_is_rejected(self):
        self.candidate["hook"] = {"episode": 1, "timestamp": "00:10.00", "quote": '"I thought..."'}
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(any("first 3 seconds" in error for error in result["errors"]))

    def test_action_hook_is_marked_for_editorial_review(self):
        self.candidate["hook"] = {"episode": 1, "timestamp": "00:00.00", "quote": "A guard kicks open the door."}
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(any("assess its strength" in warning for warning in result["warnings"]))

    def test_quoted_explanation_must_match_a_selected_source_line(self):
        self.candidate["why_it_works"] = 'The scene asks "Who told you?"'
        self.assertTrue(validate_candidate(self.candidate, self.episodes)["valid"])
        self.candidate["why_it_works"] = 'The scene asks "Where is the treasure?"'
        result = validate_candidate(self.candidate, self.episodes)
        self.assertFalse(result["valid"])
        self.assertTrue(any("quoted text" in error for error in result["errors"]))

    def test_narrative_timestamps_must_be_structured(self):
        self.candidate["why_it_works"] = "The moment ends at 00:30.00."
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(any("structured segment/hook" in error for error in result["errors"]))

    def test_duration_lower_bound_inclusive(self):
        self.candidate["segments"][0]["end"] = "00:20.00"
        self.assertTrue(validate_candidate(self.candidate, self.episodes)["valid"])
        self.candidate["segments"][0]["end"] = "00:15.00"
        self.assertTrue(any("between 20 and 60" in error for error in validate_candidate(self.candidate, self.episodes)["errors"]))

    def test_cross_episode_60_seconds_passes(self):
        self.candidate["segments"].append({"episode": 2, "start": "00:00.00", "end": "00:30.00"})
        result = validate_candidate(self.candidate, self.episodes)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["duration_seconds"], 60.0)

    def test_cross_episode_reverse_order_fails(self):
        self.candidate["segments"] = [
            {"episode": 2, "start": "00:00.00", "end": "00:20.00"},
            {"episode": 1, "start": "00:20.00", "end": "00:30.00"},
        ]
        self.candidate["hook"]["episode"] = 2
        self.assertTrue(any("episode and timestamp order" in error for error in validate_candidate(self.candidate, self.episodes)["errors"]))

    def test_same_episode_overlap_fails(self):
        self.candidate["segments"].append({"episode": 1, "start": "00:20.00", "end": "00:30.00"})
        self.assertTrue(any("must not overlap" in error for error in validate_candidate(self.candidate, self.episodes)["errors"]))

    def test_more_than_four_segments_fail(self):
        self.candidate["segments"] = [copy.deepcopy(self.candidate["segments"][0]) for _ in range(5)]
        self.assertTrue(any("at most 4" in error for error in validate_candidate(self.candidate, self.episodes)["errors"]))

    def test_invalid_shapes_are_reported_without_crashing(self):
        for bad in (None, {}, {"segments": [None]}, {"segments": [{"episode": [], "start": None, "end": "x"}]}):
            with self.subTest(bad=bad):
                self.assertFalse(validate_candidate(bad, self.episodes)["valid"])


if __name__ == "__main__":
    unittest.main()
