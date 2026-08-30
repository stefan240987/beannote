import unittest

from translations import FALLBACK_LANG, language_from_accept_header, language_from_locales, normalize_lang


class LanguageDetectionTests(unittest.TestCase):
    def test_exact_catalog_codes(self):
        self.assertEqual(normalize_lang("da"), "da")
        self.assertEqual(normalize_lang("en"), "en")

    def test_region_tags_map_to_catalog(self):
        self.assertEqual(normalize_lang("da-DK"), "da")
        self.assertEqual(normalize_lang("en-US"), "en")
        self.assertEqual(normalize_lang("en_GB"), "en")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(FALLBACK_LANG, "en")
        self.assertEqual(normalize_lang("sv-SE"), "en")
        self.assertEqual(normalize_lang("de"), "en")
        self.assertEqual(normalize_lang(""), "en")
        self.assertEqual(normalize_lang(None), "en")

    def test_device_locale_list_picks_first_supported(self):
        self.assertEqual(language_from_locales("sv-SE", "da-DK", "en-US"), "da")
        self.assertEqual(language_from_locales("nb-NO", "en-GB"), "en")
        self.assertEqual(language_from_locales("de-DE", "fr-FR"), "en")

    def test_accept_language_header_maps_like_device_locales(self):
        self.assertEqual(language_from_accept_header("da-DK,da;q=0.9,en;q=0.8"), "da")
        self.assertEqual(language_from_accept_header("en-GB,en;q=0.9"), "en")
        self.assertEqual(language_from_accept_header("sv-SE,sv;q=0.9,en;q=0.8"), "en")
        self.assertEqual(language_from_accept_header("de-DE,fr-FR;q=0.8"), "en")
        self.assertEqual(language_from_accept_header(""), "en")
        self.assertEqual(language_from_accept_header(None), "en")


if __name__ == "__main__":
    unittest.main()
