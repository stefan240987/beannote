"""Story language-map backfill: detect gaps and merge translations additively."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ["RESET_DB_ON_START"] = "false"
os.environ.setdefault("JOB_WORKER_EMBEDDED", "0")

from db import (
    apply_story_translation_result,
    detect_story_lang,
    init_db,
    insert_bean,
    list_beans_needing_story_translation,
    replace_bean_story_map,
    story_translation_plan,
    update_bean_story,
)


class StoryPlanTests(unittest.TestCase):
    def test_detects_danish_from_letters(self):
        self.assertEqual(
            detect_story_lang("Høstet i Yirgacheffe af småbønder på gården."),
            "da",
        )

    def test_detects_english_from_words(self):
        self.assertEqual(
            detect_story_lang("Harvested at the farm with roasted coffee beans."),
            "en",
        )

    def test_danish_only_needs_english(self):
        plan = story_translation_plan({"da": "Høstet i 1.900 meters højde af småbønder."})
        self.assertIsNotNone(plan)
        self.assertEqual(plan["source_lang"], "da")
        self.assertEqual(plan["target_lang"], "en")
        self.assertFalse(plan["overwrite_target"])

    def test_danish_stored_under_english_is_rekeyed(self):
        plan = story_translation_plan({"en": "Høstet i 1.900 meters højde af småbønder."})
        self.assertIsNotNone(plan)
        self.assertEqual(plan["source_lang"], "da")
        self.assertEqual(plan["target_lang"], "en")
        self.assertTrue(plan["overwrite_target"])

    def test_identical_copy_is_overwritten(self):
        text = "Høstet i 1.900 meters højde af småbønder på gården."
        plan = story_translation_plan({"da": text, "en": text})
        self.assertIsNotNone(plan)
        self.assertTrue(plan["overwrite_target"])
        self.assertEqual(plan["target_lang"], "en")

    def test_true_bilingual_story_is_left_alone(self):
        plan = story_translation_plan(
            {
                "da": "Høstet i 1.900 meters højde af småbønder på gården.",
                "en": "Harvested at 1,900 metres by smallholders on the farm.",
            }
        )
        self.assertIsNone(plan)

    def test_empty_story_has_no_plan(self):
        self.assertIsNone(story_translation_plan(""))
        self.assertIsNone(story_translation_plan({}))

    def test_merge_keeps_source_and_writes_target(self):
        danish = "Høstet i 1.900 meters højde af småbønder på gården."
        plan = story_translation_plan({"en": danish})
        merged = apply_story_translation_result(
            {"en": danish},
            plan,
            "Harvested at 1,900 metres by smallholders on the farm.",
        )
        self.assertEqual(merged["da"], danish)
        self.assertIn("Harvested", merged["en"])
        self.assertNotEqual(merged["en"], danish)


class StoryBackfillDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("BEANNOTE_DB_PATH")
        os.environ["BEANNOTE_DB_PATH"] = str(Path(self.tmp.name) / "beannote.db")
        init_db()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("BEANNOTE_DB_PATH", None)
        else:
            os.environ["BEANNOTE_DB_PATH"] = self._prev
        self.tmp.cleanup()

    def test_lists_incomplete_stories_and_saves_translation(self):
        danish = "Høstet i 1.900 meters højde af småbønder på gården."
        created = insert_bean("Yirgacheffe", "Test Risteri", story={"da": danish}, skip_fuzzy=True)
        bean_id = created["bean"]["id"]
        rows = list_beans_needing_story_translation(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], bean_id)
        english = "Harvested at 1,900 metres by smallholders on the farm."
        next_map = apply_story_translation_result(rows[0]["story"], rows[0]["plan"], english)
        replace_bean_story_map(bean_id, next_map)
        self.assertEqual(list_beans_needing_story_translation(10), [])

    def test_update_bean_story_does_not_wipe_other_language(self):
        danish = "Høstet i 1.900 meters højde af småbønder på gården."
        created = insert_bean("Guji", "Test Risteri", story={"da": danish}, skip_fuzzy=True)
        bean_id = created["bean"]["id"]
        update_bean_story(bean_id, {"en": "Harvested at 1,900 metres by smallholders on the farm."})
        from db import get_bean

        bean = get_bean(bean_id)
        self.assertEqual(bean["story"]["da"], danish)
        self.assertIn("Harvested", bean["story"]["en"])


class StoryCompleteMapTests(unittest.TestCase):
    def test_complete_story_map_uses_translator(self):
        from services import story_i18n

        danish = "Høstet i 1.900 meters højde af småbønder på gården."
        with patch.object(story_i18n, "translate_story_text", return_value="Harvested at the farm."):
            filled = story_i18n.complete_story_map({"da": danish})
        self.assertEqual(filled["da"], danish)
        self.assertEqual(filled["en"], "Harvested at the farm.")


if __name__ == "__main__":
    unittest.main()
