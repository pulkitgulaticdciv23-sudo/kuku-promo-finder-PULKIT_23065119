"""Tests for safe uploads, source display and UI control flow with a fake UI."""

import io
import csv
import json
import runpy
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fixtures import screenplay_text
from llm_client import load_config
from screenplay import load_show
from ui_helpers import (load_reviews, review_csv, review_path, save_review,
                        script_lines, store_upload, table_rows, ui_run_label)
from window_options import make_local_windows


def example_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("show/episode-001.txt", screenplay_text(1))
    return buffer.getvalue()


class StopApp(BaseException):
    pass


class FakeStreamlit(types.ModuleType):
    def __init__(self, upload=None, click=False, load_click=False):
        super().__init__("streamlit")
        self.upload = upload
        self.click = click
        self.load_click = load_click
        self.session_state = {}
        self.sidebar = self
        self.tables = []
        self.downloads = []
        self.messages = []

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def set_page_config(self, *args, **kwargs): pass
    def markdown(self, *args, **kwargs): pass
    def title(self, *args, **kwargs): pass
    def header(self, *args, **kwargs): pass
    def subheader(self, *args, **kwargs): pass
    def caption(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def write(self, *args, **kwargs): pass
    def text(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): self.messages.append(args)
    def error(self, *args, **kwargs): self.messages.append(args)
    def success(self, *args, **kwargs): pass
    def selectbox(self, label, options, **kwargs): return options[kwargs.get("index", 0)]
    def radio(self, label, options, **kwargs): return options[kwargs.get("index", 0)]
    def file_uploader(self, *args, **kwargs): return self.upload
    def stop(self): raise StopApp()
    def columns(self, n): return [self] * (n if isinstance(n, int) else len(n))
    def button(self, label, **kwargs):
        return bool((self.click and label == "Find promos") or (self.load_click and label == "Open saved results" and not kwargs.get("disabled")))
    def expander(self, *args, **kwargs): return self
    def spinner(self, *args, **kwargs): return self
    def form(self, *args, **kwargs): return self
    def form_submit_button(self, *args, **kwargs): return False
    def text_area(self, *args, **kwargs): return kwargs.get("value", "")
    def progress(self, *args, **kwargs): return self
    def empty(self): return self
    def metric(self, *args, **kwargs): pass
    def dataframe(self, data, **kwargs): self.tables.append(data)
    def json(self, *args, **kwargs): pass
    def code(self, *args, **kwargs): pass
    def download_button(self, label, **kwargs): self.downloads.append(label)


class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_zip_validation_and_content_addressing(self):
        data = example_zip()
        path, digest = store_upload(data, self.root / "uploads")
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(store_upload(data, self.root / "uploads"), (path, digest))
        with self.assertRaises(ValueError): store_upload(b"not a zip", self.root)
        blank = io.BytesIO()
        with zipfile.ZipFile(blank, "w") as archive:
            archive.writestr("image.png", b"fake")
        with self.assertRaises(ValueError): store_upload(blank.getvalue(), self.root)

    def test_run_identity_changes_with_model_or_variant(self):
        config = load_config("config.stage4.local.json")
        a = ui_run_label("a" * 64, config, "A")
        self.assertNotEqual(a, ui_run_label("a" * 64, config, "B"))
        self.assertNotEqual(a, ui_run_label("a" * 64, dict(config, model="different"), "A"))

    def test_source_text_and_table_include_segment_details(self):
        path, _ = store_upload(example_zip(), self.root / "uploads")
        episodes, _ = load_show(path)
        w = make_local_windows(episodes[1])[0]
        event = next(e for e in episodes[1]["events"] if e["event_id"] == w["hook_event_id"])
        candidate = {"rank": 1, "candidate_id": "C1", "segments": w["segments"], "duration_seconds": w["duration_seconds"],
                     "hook": {"quote": event["text"]}, "cliffhanger": "Review needed.", "final_score": 50}
        self.assertEqual(table_rows([candidate])[0]["Episode(s)"], "1")
        self.assertIn("SEGMENT 1", "\n".join(script_lines(candidate, episodes)))
        self.assertIn(event["text"], "\n".join(script_lines(candidate, episodes)))

    def test_user_review_is_separate_from_the_original_and_exported_safely(self):
        original = self.root / "manual_audit.csv"
        original.write_bytes(b"unaltered original\n")
        path = review_path(self.root / "reviews", "show-hash", "A")
        self.assertEqual(load_reviews(path), {})
        save_review(path, "C1", "Good", "=reviewed")
        save_review(path, "C2", "Unsure", "Need context")
        self.assertNotEqual(path, review_path(self.root / "reviews", "show-hash", "B"))
        ranked = [{"rank": 1, "candidate_id": "C1"}, {"rank": 2, "candidate_id": "C2"},
                  {"rank": 3, "candidate_id": "C3"}]
        rows = list(csv.DictReader(io.StringIO(review_csv(ranked, load_reviews(path)))))
        self.assertEqual([r["verdict_good_or_bad"] for r in rows], ["Good", "Unsure", ""])
        self.assertEqual(rows[0]["notes"], "'=reviewed")
        self.assertEqual(original.read_bytes(), b"unaltered original\n")
        save_review(path, "C1", "Not reviewed", "")
        self.assertNotIn("C1", load_reviews(path))

    def test_can_use_a_bundled_show_without_uploading_it(self):
        app_path = self.root / "app.py"
        shutil.copyfile("app.py", app_path)
        bundled = self.root / "input_pack" / "show.zip"
        bundled.parent.mkdir()
        bundled.write_bytes(example_zip())
        config = load_config("config.stage4.local.json")
        config["raw_outputs_dir"] = str(self.root / "raw")
        fake = FakeStreamlit()
        with patch.dict(sys.modules, {"streamlit": fake}), patch("llm_client.load_config", return_value=config), \
             patch.object(fake, "file_uploader", side_effect=AssertionError("unexpected upload")), \
             self.assertRaises(StopApp):
            runpy.run_path(str(app_path), run_name="__main__")

    def test_initial_ui_without_upload_does_not_run_the_model(self):
        fake = FakeStreamlit()
        with patch.dict(sys.modules, {"streamlit": fake}), self.assertRaises(StopApp):
            runpy.run_path("app.py", run_name="__main__")
        self.assertFalse(fake.tables)

    def test_upload_run_results_and_downloads_with_simulated_ui(self):
        data = example_zip()
        fake = FakeStreamlit(upload=types.SimpleNamespace(getvalue=lambda: data), click=True)
        real_store = store_upload
        def save_to_temp(payload, _):
            return real_store(payload, self.root / "uploads")
        def fake_execute(input_path, config, label, progress=None):
            from run_stage4 import show_fingerprint
            episodes, _ = load_show(input_path)
            option = make_local_windows(episodes[1])[0]
            event = next(e for e in episodes[1]["events"] if e["event_id"] == option["hook_event_id"])
            candidate = {"rank": 1, "candidate_id": "C1", "segments": option["segments"],
                         "duration_seconds": option["duration_seconds"], "hook": {"quote": event["text"]},
                         "cliffhanger": "Unanswered.", "why_it_works": "Conflict.", "context_needed": "None",
                         "final_score": 50, "score_breakdown": {}}
            out = self.root / "result"
            out.mkdir()
            (out / "promos.json").write_text(json.dumps({"status": "completed", "show_fingerprint": show_fingerprint(episodes),
                "metrics": {"episodes_processed": 1,
                "episodes_total": 1, "validated": 1}, "ranked": [candidate], "unscored": []}), encoding="utf-8")
            for name in ("promos.csv", "eval_report.md", "manual_audit.md", "manual_audit.csv", "ranked_report.md"):
                (out / name).write_text("test", encoding="utf-8")
            progress({"phase": "done", "done": 1, "total": 1, "message": "done"})
            return {}, out
        with patch.dict(sys.modules, {"streamlit": fake}), patch("ui_helpers.store_upload", save_to_temp), \
             patch("run_stage4.execute", fake_execute):
            runpy.run_path("app.py", run_name="__main__")
        self.assertEqual(len(fake.tables), 1)
        self.assertIn("Download CSV", fake.downloads)
        self.assertIn("Download JSON", fake.downloads)

    def test_can_open_existing_cli_result_without_running_model(self):
        from run_stage4 import show_fingerprint
        data = example_zip()
        source, _ = store_upload(data, self.root / "uploads")
        episodes, _ = load_show(source)
        option = make_local_windows(episodes[1])[0]
        event = next(e for e in episodes[1]["events"] if e["event_id"] == option["hook_event_id"])
        candidate = {"rank": 1, "candidate_id": "C1", "segments": option["segments"],
                     "duration_seconds": option["duration_seconds"], "hook": {"quote": event["text"]},
                     "cliffhanger": "Unanswered.", "why_it_works": "Conflict.", "context_needed": "None",
                     "final_score": 50, "score_breakdown": {}}
        out = self.root / "raw" / "run_A"
        out.mkdir(parents=True)
        (out / "promos.json").write_text(json.dumps({"status": "completed", "run_label": "A",
            "show_fingerprint": show_fingerprint(episodes), "metrics": {"episodes_processed": 1, "episodes_total": 1,
            "validated": 1}, "ranked": [candidate], "unscored": []}), encoding="utf-8")
        config = load_config("config.stage4.local.json")
        config["raw_outputs_dir"] = str(self.root / "raw")
        fake = FakeStreamlit(upload=types.SimpleNamespace(getvalue=lambda: data), load_click=True)
        with patch.dict(sys.modules, {"streamlit": fake}), patch("ui_helpers.store_upload", return_value=(source, "a" * 64)), \
             patch("llm_client.load_config", return_value=config), patch("run_stage4.execute", side_effect=AssertionError("model ran")):
            runpy.run_path("app.py", run_name="__main__")
        self.assertEqual(len(fake.tables), 1)


if __name__ == "__main__":
    unittest.main()
