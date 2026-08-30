import pathlib
import tempfile
import unittest
from minerva.core import ps3_dkeys


class TestPs3Dkeys(unittest.TestCase):
    def setUp(self):
        ps3_dkeys.reset_dkey_catalog_cache()

    def test_is_ps3_iso_browse_path(self):
        self.assertTrue(
            ps3_dkeys.is_ps3_iso_browse_path("/browse/./Redump/Sony - PlayStation 3/")
        )
        self.assertTrue(
            ps3_dkeys.is_ps3_iso_browse_path(
                "/browse/./Redump/Sony%20-%20PlayStation%203/"
            )
        )
        self.assertFalse(
            ps3_dkeys.is_ps3_iso_browse_path(
                "/browse/./Redump/Sony - PlayStation 3 - Disc Keys TXT/"
            )
        )
        self.assertFalse(
            ps3_dkeys.is_ps3_iso_browse_path(
                "/browse/./No-Intro/Sony - PlayStation 3 (PSN)/"
            )
        )
        self.assertFalse(ps3_dkeys.is_ps3_iso_browse_path("/browse/./Redump/Sony - PlayStation 2/"))
        self.assertFalse(ps3_dkeys.is_ps3_iso_browse_path(""))

    def test_dkey_zip_name_for_rom(self):
        self.assertEqual(
            ps3_dkeys.dkey_zip_name_for_rom("Afrika (USA).zip"),
            "Afrika (USA).zip",
        )
        self.assertEqual(
            ps3_dkeys.dkey_zip_name_for_rom("Afrika (USA).iso"),
            "Afrika (USA).zip",
        )
        self.assertEqual(ps3_dkeys.dkey_zip_name_for_rom(""), "")

    def test_find_dkey_entry_matches_catalog_name(self):
        catalog = [
            {
                "name": "Afrika (USA).zip",
                "href": "/rom?id=1066834",
                "is_folder": False,
            },
            {
                "name": "Afrika (Japan).zip",
                "href": "/rom?id=1067910",
                "is_folder": False,
            },
        ]
        ps3_dkeys._cache_index = ps3_dkeys._build_index(catalog)
        ps3_dkeys._cache_fetched_at = 10**12

        match = ps3_dkeys.find_dkey_entry("Afrika (USA).zip")
        self.assertIsNotNone(match)
        self.assertEqual(match["href"], "/rom?id=1066834")

        self.assertIsNone(ps3_dkeys.find_dkey_entry("Not A Game (USA).zip"))
        self.assertIsNone(ps3_dkeys.find_dkey_entry("Afrika.zip"))

    def test_find_dkey_entry_by_serial_and_cleaned_name(self):
        catalog = [
            {
                "name": "Assassin's Creed (USA) (BLUS-30808).zip",
                "href": "/rom?id=10",
                "is_folder": False,
            },
            {
                "name": "Assassin's Creed (Europe) (BLES-01384).zip",
                "href": "/rom?id=11",
                "is_folder": False,
            },
            {
                "name": "Unique Title (USA).zip",
                "href": "/rom?id=12",
                "is_folder": False,
            },
        ]
        ps3_dkeys._cache_index = ps3_dkeys._build_index(catalog)
        ps3_dkeys._cache_fetched_at = 10**12

        serial_match = ps3_dkeys.find_dkey_entry("Assassin's Creed (BLUS-30808).zip")
        self.assertIsNotNone(serial_match)
        self.assertEqual(serial_match["href"], "/rom?id=10")

        unique_cleaned = ps3_dkeys.find_dkey_entry("Unique Title.zip")
        self.assertIsNotNone(unique_cleaned)
        self.assertEqual(unique_cleaned["href"], "/rom?id=12")

        self.assertEqual(ps3_dkeys.serial_from_name("Game (USA) (BLUS-30808).iso"), "BLUS30808")

    def test_extract_title_ids_from_iso_header(self):
        payload = b"\x00" * 80 + b"PlayStation3" + b"\x00BLUS30853\x00" + b"\x00" * 20
        ids = ps3_dkeys.extract_title_ids_from_bytes(payload)
        self.assertIn("BLUS30853", ids)

        with tempfile.TemporaryDirectory() as tmpdir:
            iso = pathlib.Path(tmpdir) / "cleaned.iso"
            iso.write_bytes(payload + b"\x00" * 1000)
            catalog = [
                {
                    "name": "Mass Effect 3 (USA) (BLUS-30853).zip",
                    "href": "/rom?id=99",
                    "is_folder": False,
                }
            ]
            ps3_dkeys._cache_index = ps3_dkeys._build_index(catalog)
            ps3_dkeys._cache_fetched_at = 10**12
            match = ps3_dkeys.find_dkey_entry_for_path(iso)
            self.assertIsNotNone(match)
            self.assertEqual(match["href"], "/rom?id=99")

    def test_dkey_stems_and_valid_file(self):
        stems = ps3_dkeys.dkey_stems_for_rom("Persona 5 (USA) (En,Fr).zip")
        self.assertIn("Persona 5 (USA) (En,Fr)", stems)
        self.assertIn("Persona 5", stems)
        self.assertTrue(ps3_dkeys.is_dkey_save_path(r"C:\app\downloads\dkeys"))
        self.assertFalse(ps3_dkeys.is_dkey_save_path(r"C:\app\downloads"))

        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            good = base / "Persona 5.dkey"
            good.write_text("E40B60CEFFF899F952C1B35C58102355")
            self.assertTrue(ps3_dkeys.is_valid_dkey_file(good))
            bad = base / "Persona 5 (USA) (En,Fr).dkey"
            bad.write_text("not-a-key")
            self.assertFalse(ps3_dkeys.is_valid_dkey_file(bad))
            found = ps3_dkeys.find_local_dkey(base, "Persona 5 (USA) (En,Fr).zip")
            self.assertEqual(found, good)

    def test_collect_local_ps3_rom_names(self):
        catalog = [
            {"name": "Afrika (USA).zip", "href": "/rom?id=1", "is_folder": False},
        ]
        ps3_dkeys._cache_index = ps3_dkeys._build_index(catalog)
        ps3_dkeys._cache_fetched_at = 10**12
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            rom = base / "Afrika (USA).zip"
            rom.write_bytes(b"x" * (1024 * 1024 + 10))
            dkeys = base / "dkeys"
            dkeys.mkdir()
            (dkeys / "Afrika (USA).zip").write_bytes(b"tiny")
            (base / "Other Game.zip").write_bytes(b"x" * (1024 * 1024 + 10))
            names = ps3_dkeys.collect_local_ps3_rom_names(base)
            self.assertEqual(names, ["Afrika (USA).zip"])
            cleaned = base / "Afrika.zip"
            cleaned.write_bytes(b"y" * (1024 * 1024 + 10))
            names = ps3_dkeys.collect_local_ps3_rom_names(base)
            self.assertEqual(names, ["Afrika (USA).zip"])

    def test_build_index_skips_folders_and_missing_ids(self):
        index = ps3_dkeys._build_index(
            [
                {"name": "Keys/", "href": "/browse/foo/", "is_folder": True},
                {"name": "NoId.zip", "href": "/browse/file.zip", "is_folder": False},
                {"name": "Good.zip", "href": "/rom?id=42", "is_folder": False},
            ]
        )
        self.assertEqual(list(index.keys()), ["good.zip"])


if __name__ == "__main__":
    unittest.main()
