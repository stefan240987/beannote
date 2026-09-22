"""Link import translates any shop language into Danish and English."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ["RESET_DB_ON_START"] = "false"
os.environ.setdefault("BEANNOTE_DB_PATH", "/tmp/beannote-from-url-test.db")

from db import _complete_flavor_map, connect, get_bean, init_db, insert_bean
from ocr import refine_label_fields
from services.gemini import _from_url_prompt, parse_bean_from_url

_JA_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "no.17 ストロングブレンド 100g",
  "description": "【レーズンのような香りとチョコレートのような甘さ。】コロンビアの深煎り。ブラジル、エチオピア、グアテマラ。",
  "brand": {"@type": "Brand", "name": "OGAWA COFFEE LABORATORY"}
}
</script>
</head><body><p>レーズンのような香り</p></body></html>
"""

_FR_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "Product",
  "name": "Mélange du matin",
  "description": "Notes de noisette et de chocolat. Origine Brésil.",
  "brand": {"@type": "Brand", "name": "Café Lumière"}
}
</script>
</head><body><p>Notes de noisette et de chocolat.</p></body></html>
"""

_JA_PAGE = "レーズンのような香りとチョコレートのような甘さ。コロンビアの深煎り。"


def _translated() -> dict:
    return {
        "name": "No. 17 Strong Blend",
        "roaster": "Translated Roaster",
        "origin": "Colombia, Brasilien, Etiopien, Guatemala",
        "roast_level": "Dark",
        "process": "",
        "flavor_notes": {
            "da": ["Rosin", "Chokolade"],
            "en": ["Raisin", "Chocolate"],
        },
        "story": {
            "da": "En blend med rosin og chokolade, bygget op om mørkristet Colombia.",
            "en": "A blend of raisin and chocolate, built around dark-roasted Colombia.",
        },
    }


class FromUrlLanguageTests(unittest.TestCase):
    def _parse(self, html: str, payload, lang: str = "da"):
        prompts: list[str] = []

        def generate(_key, prompt, _timeout, tools=None):
            del tools
            prompts.append(prompt)
            if isinstance(payload, Exception):
                raise payload
            return payload

        with patch("services.gemini.get_gemini_api_key", return_value="test-key"), \
             patch("services.gemini._load_product_page", return_value=(html, _JA_PAGE)), \
             patch("services.gemini._gemini_generate_json", side_effect=generate), \
             patch("services.gemini._cache_product_image", return_value=""), \
             patch("services.gemini._with_scan_matches", side_effect=lambda parsed: parsed):
            draft = parse_bean_from_url("https://oc-shop.co.jp/products/lab_200003", lang)
        return draft, prompts

    def test_prompt_translates_every_source_language(self):
        prompt = _from_url_prompt("ページ", "https://shop.example/coffee", [], "da")
        self.assertIn("Danish", prompt)
        self.assertIn("English", prompt)
        self.assertIn('"da"', prompt)
        self.assertIn('"en"', prompt)
        self.assertNotIn("when the page language allows", prompt)
        self.assertIn("any language", prompt)

    def test_japanese_page_keeps_printed_name_and_writes_both_languages(self):
        draft, prompts = self._parse(_JA_HTML, _translated())
        self.assertEqual(len(prompts), 1)
        self.assertEqual(draft["name"], "no.17 ストロングブレンド 100g")
        self.assertEqual(draft["roaster"], "OGAWA COFFEE LABORATORY")
        self.assertIn("rosin", draft["story"]["da"].lower())
        self.assertIn("raisin", draft["story"]["en"].lower())
        self.assertNotIn("レーズン", draft["story"]["da"])
        self.assertNotIn("レーズン", draft["roaster_notes"])
        self.assertEqual(draft["roast_level"], "Mørk")
        for country in ("Colombia", "Brasilien", "Etiopien", "Guatemala"):
            self.assertIn(country, draft["origin"])
        self.assertIn("Chokolade", draft["flavor_tags"]["da"])
        self.assertIn("Rosin", draft["flavor_tags"]["da"])
        self.assertNotIn("Raisin", draft["flavor_tags"]["da"])
        self.assertNotIn("Creamy body", draft["flavor_tags"]["da"])
        self.assertIn("Chocolate", draft["flavor_tags"]["en"])
        self.assertIn("Raisin", draft["flavor_tags"]["en"])
        self.assertNotIn("Rosin", draft["flavor_tags"]["en"])
        self.assertNotIn("Cremet fylde", draft["flavor_tags"]["en"])

    def test_echoed_japanese_copy_is_not_stored_as_danish(self):
        echoed = {
            "name": "no.17 ストロングブレンド 100g",
            "roaster": "OGAWA COFFEE LABORATORY",
            "origin": "コロンビア、ブラジル",
            "roast_level": "深煎り",
            "flavor_notes": {"da": ["レーズン", "チョコレート"], "en": ["レーズン"]},
            "story": {
                "da": "【レーズンのような香りとチョコレートのような甘さ。】コロンビアの深煎り。",
                "en": "【レーズンのような香りとチョコレートのような甘さ。】コロンビアの深煎り。",
            },
        }
        draft, _prompts = self._parse(_JA_HTML, echoed)
        self.assertEqual(draft["name"], "no.17 ストロングブレンド 100g")
        story = draft.get("story") or {}
        blob = " ".join(str(part) for part in (story.values() if isinstance(story, dict) else [story]))
        self.assertNotIn("レーズン", blob)
        self.assertNotIn("レーズン", draft.get("roaster_notes") or "")
        self.assertNotIn("コロンビア", draft.get("origin") or "")
        self.assertNotIn("深煎り", draft.get("roast_level") or "")

    def test_gemini_outage_still_creates_from_page_facts(self):
        draft, _prompts = self._parse(_JA_HTML, RuntimeError("quota"))
        self.assertEqual(draft["name"], "no.17 ストロングブレンド 100g")
        self.assertEqual(draft["roaster"], "OGAWA COFFEE LABORATORY")
        self.assertEqual(draft["scan_enrichment"], "url+jsonld")

    def test_french_page_uses_the_translation_not_the_shop_copy(self):
        payload = {
            "name": "Morning blend",
            "roaster": "Light Coffee",
            "origin": "Brasilien",
            "roast_level": "Medium",
            "flavor_notes": {"da": ["Hasselnød", "Chokolade"], "en": ["Hazelnut", "Chocolate"]},
            "story": {
                "da": "En morgenblend med hasselnød og chokolade fra Brasilien.",
                "en": "A morning blend with hazelnut and chocolate from Brazil.",
            },
        }
        draft, prompts = self._parse(_FR_HTML, payload)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(draft["name"], "Mélange du matin")
        self.assertEqual(draft["roaster"], "Café Lumière")
        self.assertIn("hasselnød", draft["story"]["da"].lower())
        self.assertNotIn("noisette", draft["story"]["da"].lower())
        self.assertIn("hazelnut", draft["story"]["en"].lower())

    def test_multi_country_origin_keeps_every_country(self):
        refined = refine_label_fields(
            {
                "name": "Blend",
                "origin": "Colombia, Brasilien, Etiopien, Guatemala",
                "story": {"da": "Fire oprindelser, heriblandt Brasilien og Etiopien."},
            },
            lang="da",
        )
        for country in ("Colombia", "Brasilien", "Etiopien", "Guatemala"):
            self.assertIn(country, refined["origin"])


class FlavorLanguageRepairTests(unittest.TestCase):
    def test_clean_bilingual_notes_stay_apart(self):
        mapped = _complete_flavor_map(
            {
                "da": ["Rosin", "Chokolade", "Cremet fylde"],
                "en": ["Raisin", "Chocolate", "Creamy body"],
            }
        )
        self.assertEqual(mapped["da"], ["Chokolade", "Rosin", "Cremet fylde"])
        self.assertEqual(mapped["en"], ["Chocolate", "Raisin", "Creamy body"])

    def test_copied_notes_are_split_back(self):
        mixed = ["Chokolade", "Rosin", "Cremet fylde", "Raisin", "Creamy body", "Chocolate"]
        mapped = _complete_flavor_map({"da": list(mixed), "en": list(mixed)})
        self.assertIn("Rosin", mapped["da"])
        self.assertIn("Cremet fylde", mapped["da"])
        self.assertNotIn("Raisin", mapped["da"])
        self.assertNotIn("Creamy body", mapped["da"])
        self.assertIn("Raisin", mapped["en"])
        self.assertIn("Creamy body", mapped["en"])
        self.assertNotIn("Rosin", mapped["en"])
        self.assertNotIn("Cremet fylde", mapped["en"])

    def test_startup_rewrites_stored_flavor_maps(self):
        import json
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        previous = os.environ.get("BEANNOTE_DB_PATH")
        os.environ["BEANNOTE_DB_PATH"] = str(Path(tmp.name) / "beannote.db")
        try:
            init_db()
            created = insert_bean(
                "no.17 Strong",
                "Ogawa Test",
                flavor_tags={"da": ["Chokolade"], "en": ["Chocolate"]},
                skip_fuzzy=True,
            )
            bean_id = created["bean"]["id"]
            mixed = {
                "da": ["Chokolade", "Rosin", "Cremet fylde", "Raisin", "Creamy body"],
                "en": ["Chocolate", "Rosin", "Cremet fylde", "Raisin", "Creamy body"],
            }
            with connect() as conn:
                conn.execute(
                    "UPDATE beans SET flavor_tags = ? WHERE id = ?",
                    (json.dumps(mixed, ensure_ascii=False), bean_id),
                )
            init_db()
            with connect() as conn:
                stored = json.loads(
                    conn.execute(
                        "SELECT flavor_tags FROM beans WHERE id = ?",
                        (bean_id,),
                    ).fetchone()["flavor_tags"]
                )
            self.assertIn("Rosin", stored["da"])
            self.assertNotIn("Raisin", stored["da"])
            self.assertIn("Raisin", stored["en"])
            self.assertNotIn("Rosin", stored["en"])
            bean = get_bean(bean_id)
            self.assertNotIn("Raisin", bean["flavor_tags"]["da"])
            self.assertNotIn("Rosin", bean["flavor_tags"]["en"])
        finally:
            if previous is None:
                os.environ.pop("BEANNOTE_DB_PATH", None)
            else:
                os.environ["BEANNOTE_DB_PATH"] = previous
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
