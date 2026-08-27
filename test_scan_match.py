"""Archive matching for generic bag titles from the same roaster."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db import (
    SCAN_MATCH_CUTOFF,
    find_similar_beans,
    is_generic_bean_name,
    origins_conflict,
    qualify_generic_bean_name,
    regions_conflict,
    scan_destination,
)


def _bean(
    bean_id: int,
    name: str,
    roaster: str,
    origin: str = "",
    region: str = "",
    varietal: str = "",
) -> dict:
    return {
        "id": bean_id,
        "name": name,
        "roaster": roaster,
        "origin": origin,
        "region_full": region,
        "varietal": varietal,
    }


class GenericNameTests(unittest.TestCase):
    def test_espresso_and_slow_roast_are_generic(self):
        self.assertTrue(is_generic_bean_name("Espresso"))
        self.assertTrue(is_generic_bean_name("Slow Roast Espresso"))
        self.assertTrue(is_generic_bean_name("Filter"))
        self.assertFalse(is_generic_bean_name("Uno"))
        self.assertFalse(is_generic_bean_name("Espresso Cerrado Mineiro"))

    def test_qualifies_generic_title_with_region(self):
        self.assertEqual(
            qualify_generic_bean_name("Espresso", "Brasilien", "Cerrado Mineiro"),
            "Espresso Cerrado Mineiro",
        )
        self.assertEqual(
            qualify_generic_bean_name("Espresso", "Brasilien", "CERRADO MINEIRO"),
            "Espresso Cerrado Mineiro",
        )
        self.assertEqual(
            qualify_generic_bean_name("Espresso", "Brasilien", ""),
            "Espresso Brasilien",
        )
        self.assertEqual(qualify_generic_bean_name("Uno", "Brasilien", ""), "Uno")

    def test_brazil_and_brasilien_do_not_conflict(self):
        self.assertFalse(origins_conflict("Brazil", "Brasilien"))
        self.assertTrue(origins_conflict("Brasilien", "Colombia"))
        self.assertFalse(origins_conflict("Brasilien", ""))

    def test_cerrado_and_sul_de_minas_conflict(self):
        self.assertTrue(regions_conflict("Cerrado Mineiro", "Sul de Minas"))
        self.assertFalse(regions_conflict("Cerrado Mineiro, Brazil", "Cerrado Mineiro"))


class ScanMatchTests(unittest.TestCase):
    def test_different_origin_espresso_is_not_the_same_bag(self):
        archive = [
            _bean(1, "Espresso", "Copenhagen Roaster", origin="Colombia"),
        ]
        with patch("db.list_beans", return_value=archive):
            hits = find_similar_beans(
                "Espresso",
                "Copenhagen Roaster",
                origin="Brasilien",
                region="Cerrado Mineiro",
                varietal="Catuai",
            )
        self.assertEqual(hits, [])
        self.assertEqual(scan_destination(hits), "add")

    def test_same_origin_and_region_opens_existing(self):
        archive = [
            _bean(
                1,
                "Espresso",
                "Copenhagen Roaster",
                origin="Brasilien",
                region="Cerrado Mineiro",
                varietal="Catuai",
            ),
        ]
        with patch("db.list_beans", return_value=archive):
            hits = find_similar_beans(
                "Espresso",
                "Copenhagen Roaster",
                origin="Brazil",
                region="Cerrado Mineiro",
                varietal="Catuai",
            )
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0]["confidence"], SCAN_MATCH_CUTOFF)
        self.assertEqual(scan_destination(hits), "rate")

    def test_generic_name_without_origin_does_not_auto_open(self):
        archive = [
            _bean(1, "Espresso", "Copenhagen Roaster", origin="Colombia"),
        ]
        with patch("db.list_beans", return_value=archive):
            hits = find_similar_beans("Espresso", "Copenhagen Roaster")
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0]["confidence"], SCAN_MATCH_CUTOFF)
        self.assertEqual(scan_destination(hits), "add")

    def test_distinctive_names_still_match(self):
        archive = [_bean(2, "Uno", "Risteriet", origin="Blend")]
        with patch("db.list_beans", return_value=archive):
            hits = find_similar_beans("Uno", "Risteriet")
        self.assertEqual(len(hits), 1)
        self.assertEqual(scan_destination(hits), "rate")

    def test_filter_does_not_match_espresso_same_origin(self):
        archive = [_bean(3, "Espresso", "Copenhagen Roaster", origin="Brasilien")]
        with patch("db.list_beans", return_value=archive):
            hits = find_similar_beans(
                "Filter",
                "Copenhagen Roaster",
                origin="Brasilien",
            )
        self.assertEqual(hits, [])


class CopenhagenRoasterLabelTests(unittest.TestCase):
    def test_label_prefers_espresso_and_cerrado_over_slow_roast(self):
        from ocr import parse_label, refine_label_fields

        text = """
        COPENHAGEN ROASTER Est. 2005
        SLOW ROAST
        Espresso
        HELE BØNNER • MØRKRISTET • ARABICA
        OPRINDELSE
        BRASILIEN
        HØJDEMETER
        850 M
        SORT
        CATUAI
        REGION
        CERRADO MINEIRO
        FORARBEJDNING
        NATURAL
        750 G
        """
        parsed = refine_label_fields(parse_label(text), lang="da")
        self.assertEqual(parsed["name"], "Espresso Cerrado Mineiro")
        self.assertEqual(parsed["roaster"], "Copenhagen Roaster")
        self.assertEqual(parsed["origin"], "Brasilien")
        self.assertEqual(parsed["process"], "Natural")
        self.assertEqual(parsed["varietal"], "Catuai")
        self.assertEqual(parsed["region_full"], "Cerrado Mineiro")


if __name__ == "__main__":
    unittest.main()
