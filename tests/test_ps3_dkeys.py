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
