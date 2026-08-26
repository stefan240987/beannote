"""Grounded coffee-profile extraction for Puro Compañero and Risteriet Uno."""

from __future__ import annotations

import unittest

from db import infer_intensity_scores
from ocr import (
    count_circle_meters,
    extract_flavor_canons,
    flavor_appears_in_text,
    flavor_tags_lang_map,
    ground_extracted_fields,
    grounded_flavor_tags,
    html_to_visible_text,
    infer_brew_recommendation,
    is_mouthfeel_tag,
    is_unwanted_product_url,
    normalize_scan_fields,
)


PURO_HTML = """
<h2>Kaffeprofil:</h2>
<p><i class="uk-icon-circle" style="color:#572702;"></i> <i class="uk-icon-circle" style="color:#572702;"></i> <i class="uk-icon-circle" style="color:#572702;"></i> <i class="uk-icon-circle" style="color:#572702;"></i> <i class="uk-icon-circle" style="color:#572702;"></i> &nbsp;&nbsp;&nbsp;Ristning<br>
<i class="uk-icon-circle" style="color:#8595a1;"></i> <i class="uk-icon-circle" style="color:#8595a1;"></i> <i class="uk-icon-circle" style="color:#8595a1;"></i> <i class="uk-icon-circle" style="color:#8595a1;"></i> <i class="uk-icon-circle" style="color:#8595a1;"></i> &nbsp;&nbsp;&nbsp;Krop<br>
<i class="uk-icon-circle" style="color:#f5df8e;"></i> <i class="uk-icon-circle" style="color:#f5df8e;"></i> <i class="uk-icon-circle" style="color:#f5df8e;"></i> <i class="uk-icon-circle" style="color:#f5df8e;"></i> <i class="uk-icon-circle-o" style="color:#f5df8e;"></i> &nbsp;&nbsp;&nbsp;Sød<br>
<i class="uk-icon-circle" style="color:#b8d5a5;"></i> <i class="uk-icon-circle" style="color:#b8d5a5;"></i> <i class="uk-icon-circle-o" style="color:#b8d5a5;"></i> <i class="uk-icon-circle-o" style="color:#b8d5a5;"></i> <i class="uk-icon-circle-o" style="color:#b8d5a5;"></i> &nbsp;&nbsp;&nbsp;Syrlighed<br>
<i class="uk-icon-circle" style="color:#231f20;"></i> <i class="uk-icon-circle" style="color:#231f20;"></i> <i class="uk-icon-circle" style="color:#231f20;"></i> <i class="uk-icon-circle-o" style="color:#231f20;"></i> <i class="uk-icon-circle-o" style="color:#231f20;"></i> &nbsp;&nbsp;&nbsp;Krydret<br>
<i class="uk-icon-circle" style="color:#df7f1b;"></i> <i class="uk-icon-circle" style="color:#df7f1b;"></i> <i class="uk-icon-circle" style="color:#df7f1b;"></i> <i class="uk-icon-circle" style="color:#df7f1b;"></i> <i class="uk-icon-circle" style="color:#df7f1b;"></i> &nbsp;&nbsp;&nbsp;Chokolade</p>
"""

COMPANERO_NOTES = (
    "Gode ting kommer ofte i par! Et harmonisk ægteskab mellem arabica og robusta bønnerne. "
    "Der hvor det bløde, søde og frugtagtige møder “jord”- bitter og nøddeagtig – i en blanding "
    "der giver en kraftig, men på samme tid også blød, kop kaffe."
)

UNO_NOTES = (
    "Fantastisk aromatisk og friskhed med frugt fornemmelse. Mandarin, mørk chokolade og mandel. "
    "Uno er en fyldig blend med god sødme, mørkere toner og fantastisk naturlig crema. "
    "Fin afrundet eftersmag med flot karamel note. "
    "Smagsnoter & smagsoplevelse: Balanceret eftersmag, Blød & rund, Dyb smag, Mandarin, Mandler, Mørk chokolade. "
    "Ristet til Full city (mellemmørk rist). Espresso er hvad UNO er designet til."
)


class FlavorExtractionTests(unittest.TestCase):
    def test_companero_notes_keep_tasting_words_not_mouthfeel(self):
        tags = extract_flavor_canons(COMPANERO_NOTES)
        self.assertIn("Nøddet", tags)
        self.assertIn("Frugtagtig", tags)
        self.assertIn("Jordagtig", tags)
        self.assertNotIn("Mørk chokolade", tags)
        self.assertNotIn("Chokolade", tags)
        for banned in ("Blød", "Sød", "Bitter", "Sweet", "Smooth"):
            self.assertNotIn(banned, tags)

    def test_uno_notes_keep_published_flavors(self):
        tags = extract_flavor_canons(UNO_NOTES)
        self.assertIn("Mandarin", tags)
        self.assertIn("Mørk chokolade", tags)
        self.assertIn("Mandel", tags)
        self.assertIn("Karamel", tags)
        self.assertNotIn("Blød", tags)
        self.assertFalse(any("rund" in tag.lower() for tag in tags))

    def test_grounding_drops_invented_chocolate_on_companero(self):
        fake = {
            "flavor_tags": {
                "da": ["Mørk chokolade", "Nøddet", "Sød", "Blød", "Frugtagtig"],
                "en": ["Dark chocolate", "Nutty", "Sweet", "Smooth", "Fruity"],
            },
            "official_notes": COMPANERO_NOTES,
        }
        grounded = ground_extracted_fields(fake, page_text=COMPANERO_NOTES, html=PURO_HTML, lang="da")
        da = grounded["flavor_tags"].get("da") or []
        self.assertIn("Nøddet", da)
        self.assertIn("Frugtagtig", da)
        self.assertNotIn("Mørk chokolade", da)
        self.assertNotIn("Sød", da)
        self.assertNotIn("Blød", da)
        self.assertNotIn("Sweet", da)

    def test_language_map_does_not_leak_english_into_danish(self):
        mapped = flavor_tags_lang_map(
            {
                "da": ["Nøddet", "Frugtagtig"],
                "en": ["Nutty", "Fruity", "Sweet", "Smooth"],
            }
        )
        da = mapped.get("da") or []
        self.assertNotIn("Sweet", da)
        self.assertNotIn("Smooth", da)
        self.assertNotIn("Fruity", da)

    def test_mouthfeel_helper(self):
        self.assertTrue(is_mouthfeel_tag("Blød & rund"))
        self.assertTrue(is_mouthfeel_tag("Sweet"))
        self.assertFalse(is_mouthfeel_tag("Mandarin"))


class MeterAndScoreTests(unittest.TestCase):
    def test_puro_circle_meters(self):
        meters = count_circle_meters(PURO_HTML)
        self.assertEqual(meters["roast"], 5)
        self.assertEqual(meters["body"], 5)
        self.assertEqual(meters["sweet"], 4)
        self.assertEqual(meters["acidity"], 2)
        self.assertEqual(meters["spicy"], 3)
        self.assertEqual(meters["chocolate"], 5)

    def test_html_meters_override_guessed_scores(self):
        payload = {
            "roaster_acidity": 4,
            "roaster_body": 4,
            "roaster_roast_level": 4,
            "flavor_tags": {"da": ["Nøddet"]},
            "official_notes": COMPANERO_NOTES,
        }
        grounded = ground_extracted_fields(payload, page_text=COMPANERO_NOTES, html=PURO_HTML)
        self.assertEqual(grounded["acidity_score"], 2)
        self.assertEqual(grounded["body_score"], 5)
        self.assertEqual(grounded["roast_level_score"], 5)

    def test_uno_has_no_circle_meters(self):
        html = "<h2>Smagsprofil</h2><p>Mandarin, mørk chokolade og mandel.</p>"
        self.assertEqual(count_circle_meters(html), {})

    def test_scores_are_not_inferred_from_dark_roast(self):
        scores = infer_intensity_scores(None, None, None, "Mørk", "Peru", "Vasket", "Companero")
        self.assertIsNone(scores["acidity_score"])
        self.assertIsNone(scores["body_score"])
        self.assertIsNone(scores["roast_level_score"])

    def test_normalize_does_not_invent_brew_ratio(self):
        parsed = normalize_scan_fields(
            {
                "bean_name": "Uno",
                "roaster": "Risteriet",
                "roast_level": "Mellemmørk / Full City",
                "official_notes": UNO_NOTES,
                "flavor_tags": {"da": ["Mandarin", "Mørk chokolade", "Mandel"]},
            },
            lang="da",
        )
        self.assertFalse((parsed.get("brew_ratio") or "").strip())
        self.assertIsNone(parsed.get("acidity_score"))
        brew = parsed.get("brew_recommendation") or {}
        da = brew.get("da") if isinstance(brew, dict) else {}
        self.assertFalse((da or {}).get("brew_ratio"))

    def test_published_brew_usage_is_kept(self):
        brew = infer_brew_recommendation(
            {
                "brew_recommendation": {
                    "da": {
                        "recommended_method": "Espresso",
                        "grind_size": "",
                        "water_temp": "",
                        "brew_ratio": "",
                        "usage": "Espresso er hvad UNO er designet til.",
                    }
                }
            }
        )
        self.assertEqual(brew["da"]["recommended_method"], "Espresso")
        self.assertEqual(brew["da"]["usage"], "Espresso er hvad UNO er designet til.")
        self.assertFalse(brew["da"]["brew_ratio"])
        self.assertFalse(brew["da"]["water_temp"])


class UrlAndHtmlTests(unittest.TestCase):
    def test_rejects_green_bean_url(self):
        url = "https://www.risteriet.dk/online-shop/28-groenneraa-kaffeboenner/103-miscela-uno-raa-kaffe/"
        self.assertTrue(is_unwanted_product_url(url, "Miscela Uno"))

    def test_keeps_roasted_uno_url(self):
        url = "https://www.risteriet.dk/online-shop/41-nyristede-kaffeboenner/493-miscela-uno/"
        self.assertFalse(is_unwanted_product_url(url, "Miscela Uno"))

    def test_focus_skips_shop_chrome(self):
        from ocr import focus_product_text
        blob = ("Kaffe Kaffeabonnement Baristashop " * 80) + "Miscela Uno Smagsprofil Mandarin, mørk chokolade og mandel."
        focused = focus_product_text(blob, "Miscela Uno", "Risteriet")
        self.assertIn("Mandarin", focused)
        self.assertLess(focused.find("Smagsprofil"), 600)

    def test_meter_strip_keeps_uno_sentence(self):
        from ocr import strip_meter_label_lines
        text = "Mandarin, mørk chokolade og mandel.\nRistning Krop Sød Syrlighed Krydret Chokolade"
        cleaned = strip_meter_label_lines(text)
        self.assertIn("mørk chokolade", cleaned)
        self.assertNotIn("Syrlighed", cleaned)

    def test_html_to_text_strips_scripts(self):
        text = html_to_visible_text("<script>evil()</script><p>Mandarin og mandel</p>")
        self.assertIn("Mandarin", text)
        self.assertNotIn("evil", text)

    def test_chocolate_meter_label_is_not_a_flavor_hit_without_tasting_word(self):
        self.assertFalse(flavor_appears_in_text("Mørk chokolade", "Ristning Krop Sød Syrlighed Krydret Chokolade"))
        self.assertTrue(flavor_appears_in_text("Mørk chokolade", UNO_NOTES))


class EndToEndPageCopyTests(unittest.TestCase):
    def test_companero_profile_from_page_copy(self):
        optical = {
            "name": "Companero",
            "roaster": "Puro",
            "roaster_acidity": 4,
            "roaster_body": 4,
            "roaster_roast_level": 4,
            "flavor_tags": {"da": ["Mørk chokolade", "Sweet", "Smooth"]},
        }
        official = {
            "official_notes": COMPANERO_NOTES,
            "flavor_tags": {"da": ["Mørk chokolade", "Nøddet", "Frugtagtig", "Jordagtig", "Blød"]},
            "roast_level": "Mørk",
        }
        merged = {**optical, **official}
        grounded = ground_extracted_fields(merged, page_text=COMPANERO_NOTES, html=PURO_HTML, lang="da")
        self.assertEqual(grounded["roast_level_score"], 5)
        self.assertEqual(grounded["body_score"], 5)
        self.assertEqual(grounded["acidity_score"], 2)
        da = grounded["flavor_tags"]["da"]
        self.assertEqual(set(da) & {"Nøddet", "Frugtagtig", "Jordagtig"}, {"Nøddet", "Frugtagtig", "Jordagtig"})
        self.assertNotIn("Mørk chokolade", da)

    def test_uno_profile_from_page_copy(self):
        official = {
            "official_notes": UNO_NOTES,
            "flavor_tags": {
                "da": ["Mandarin", "Mørk chokolade", "Mandel", "Karamel", "Blød & rund", "Dyb smag"]
            },
            "roast_level": "Full city (mellemmørk rist)",
        }
        grounded = ground_extracted_fields(official, page_text=UNO_NOTES, html="", lang="da")
        da = grounded["flavor_tags"]["da"]
        self.assertIn("Mandarin", da)
        self.assertIn("Mørk chokolade", da)
        self.assertIn("Mandel", da)
        self.assertIn("Karamel", da)
        self.assertFalse(any("rund" in tag.lower() or tag == "Dyb smag" for tag in da))
        self.assertIsNone(grounded.get("acidity_score") or grounded.get("roaster_acidity"))


if __name__ == "__main__":
    unittest.main()
