import tempfile
import unittest
import zipfile
from pathlib import Path

from fixtures import screenplay_text, write_show
from screenplay import load_show, show_counts, time_to_us, us_to_timestamp


class TimestampTests(unittest.TestCase):
    def test_exact_fraction_and_minutes(self):
        self.assertEqual(time_to_us("01:02.75"), 62_750_000)
        self.assertEqual(time_to_us("00:02.7"), 2_700_000)
        self.assertEqual(time_to_us("00:02.000001"), 2_000_001)
        self.assertEqual(us_to_timestamp(62_750_000), "01:02.75")

    def test_invalid_timestamps_are_rejected(self):
        for value in ("00:60.00", "00:02.1234567", "abc", "-01:02", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                time_to_us(value)


class ScreenplayTests(unittest.TestCase):
    def test_folder_metadata_events_and_speaker_qualifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            episodes, warnings = load_show(write_show(tmp))
            self.assertEqual(warnings, [])
            self.assertEqual(show_counts(episodes)["episodes"], 2)
            self.assertEqual(episodes[1]["scenes"][0]["heading"], "INT. ROOM – DAY")
            self.assertEqual(episodes[2]["scenes"][0]["end"], "00:50.00")
            bob = next(event for event in episodes[1]["events"] if event["speaker"] == "BOB")
            self.assertEqual(bob["emotion"], "Worried")
            self.assertEqual(bob["speaker_qualifiers"], ["O.S."])
            self.assertEqual(bob["text"], '"Who told you?"')

    def test_nested_zip_is_read_without_extracting(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "show.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("nested/episode-001.txt", screenplay_text())
                archive.writestr("__MACOSX/._episode-999.txt", "not a screenplay")
            episodes, warnings = load_show(archive_path)
            self.assertEqual(list(episodes), [1])
            self.assertEqual(warnings, [])
            self.assertFalse((Path(tmp) / "nested").exists())

    def test_malformed_event_is_skipped_and_other_events_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = screenplay_text().replace(
                "Start: (00:01.00) | End: (00:03.00)",
                "Start: (00:61.00) | End: (00:03.00)",
            )
            (Path(tmp) / "episode-001.txt").write_text(bad, encoding="utf-8")
            episodes, warnings = load_show(tmp)
            self.assertTrue(any("bad timing" in warning for warning in warnings))
            self.assertFalse(any(event["text"] == '"You lied to me."' for event in episodes[1]["events"]))
            self.assertTrue(any(event["text"] == '"Who told you?"' for event in episodes[1]["events"]))

    def test_missing_timing_is_skipped_and_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = screenplay_text().replace("Start: (00:01.00) | End: (00:03.00)", "BROKEN TIMING")
            (Path(tmp) / "episode-001.txt").write_text(bad, encoding="utf-8")
            episodes, warnings = load_show(tmp)
            self.assertTrue(any("malformed timing" in warning for warning in warnings))
            self.assertEqual(len(episodes[1]["events"]), 10)

    def test_out_of_scene_event_is_preserved_and_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = screenplay_text().replace("End: (00:50.00)", "End: (00:50.20)")
            (Path(tmp) / "episode-001.txt").write_text(text, encoding="utf-8")
            episodes, warnings = load_show(tmp)
            self.assertTrue(any("extends beyond scene" in warning for warning in warnings))
            self.assertTrue(any(event["end"] == "00:50.20" for event in episodes[1]["events"]))

    def test_missing_metadata_falls_back_to_filename_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = screenplay_text().replace("EPISODE NUMBER: Episode 1\n", "")
            (Path(tmp) / "episode-001.txt").write_text(text, encoding="utf-8")
            episodes, warnings = load_show(tmp)
            self.assertEqual(list(episodes), [1])
            self.assertTrue(any("using filename number" in warning for warning in warnings))

    def test_duplicate_episode_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "episode-001-a.txt").write_text(screenplay_text(), encoding="utf-8")
            (Path(tmp) / "episode-001-b.txt").write_text(screenplay_text(), encoding="utf-8")
            episodes, warnings = load_show(tmp)
            self.assertEqual(len(episodes), 1)
            self.assertTrue(any("duplicate episode" in warning for warning in warnings))

    def test_bad_utf8_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "episode-001.txt").write_bytes(b"\xff\xfe")
            episodes, warnings = load_show(tmp)
            self.assertEqual(episodes, {})
            self.assertTrue(any("not valid UTF-8" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
