import unittest
from minerva.core.companions import (
    KIND_BASE,
    KIND_DLC,
    KIND_UPDATE,
    KIND_IGNORE,
    classify_release,
    title_key,
    names_match_companion,
    companion_folder_paths,
    find_companions,
    version_key,
    reset_companion_cache,
    system_key,
    switch_ids_related,
)


class TestCompanions(unittest.TestCase):
    def setUp(self):
        reset_companion_cache()

    def test_classify_release_flags(self):
        self.assertEqual(
            classify_release("100% Pascal Sensei - Kanpeki Paint Bombers (Japan) (DLC).zip"),
            KIND_DLC,
        )
        self.assertEqual(
            classify_release("Assassin's Creed III (USA) (v1.04) (Update).zip"),
            KIND_UPDATE,
        )
        self.assertEqual(
            classify_release("100% Pascal Sensei - Kanpeki Paint Bombers (Japan).zip"),
            KIND_BASE,
        )
        self.assertEqual(classify_release("[BIOS] HOME Menu (USA).zip"), KIND_IGNORE)
        self.assertEqual(
            classify_release("Bayonetta 2 [01004A4000B3A800][v65536].nsp"),
            KIND_UPDATE,
        )
        self.assertEqual(
            classify_release("Bayonetta 2 DLC Pack 1 [01004A4000B3B001][v0].nsp"),
            KIND_DLC,
        )
        self.assertEqual(
            classify_release("Extra Pack.zip", "Sony - PlayStation 3 (PSN) (DLC)"),
            KIND_DLC,
        )

    def test_title_key_strips_dlc_and_ids(self):
        self.assertEqual(
            title_key("100% Pascal Sensei - Kanpeki Paint Bombers (Japan) (DLC).zip"),
            title_key("100% Pascal Sensei - Kanpeki Paint Bombers (Japan).zip"),
        )
        self.assertEqual(
            title_key("Bayonetta 2 [01004A4000B3A000][v0].nsp"),
            title_key("Bayonetta 2 (USA).nsp"),
        )

    def test_names_match_companion_strict(self):
        self.assertTrue(
            names_match_companion(
                "100% Pascal Sensei - Kanpeki Paint Bombers (Japan).zip",
                "100% Pascal Sensei - Kanpeki Paint Bombers (Japan) (DLC).zip",
            )
        )
        self.assertFalse(
            names_match_companion(
                "Super Mario World (USA).zip",
                "Super Mario Galaxy (USA).zip",
            )
        )
        self.assertFalse(
            names_match_companion(
                "Persona 5 (USA).zip",
                "Persona 5 (Japan) (DLC).zip",
            )
        )
        self.assertTrue(
            names_match_companion(
                "Bayonetta 2 [01004A4000B3A000][v0].nsp",
                "Bayonetta 2 [01004A4000B3B001][v0].nsp",
            )
        )
        self.assertTrue(switch_ids_related("01004A4000B3A000", "01004A4000B3A800"))

    def test_system_key_and_companion_folders(self):
        self.assertEqual(
            system_key("Nintendo - Nintendo 3DS (Digital) (CDN)"),
            system_key("Nintendo - Nintendo 3DS"),
        )
        siblings = [
            {"name": "Nintendo - Nintendo 3DS/", "href": "/browse/./No-Intro/Nintendo - Nintendo 3DS/", "is_folder": True},
            {"name": "Nintendo - Nintendo 3DS (Digital) (CDN)/", "href": "/browse/./No-Intro/Nintendo - Nintendo 3DS (Digital) (CDN)/", "is_folder": True},
            {"name": "Nintendo - Nintendo 64/", "href": "/browse/./No-Intro/Nintendo - Nintendo 64/", "is_folder": True},
        ]
        paths = companion_folder_paths(
            "/browse/./No-Intro/Nintendo - Nintendo 3DS/",
            siblings,
        )
        self.assertIn("/browse/./No-Intro/Nintendo - Nintendo 3DS/", paths)
        self.assertIn("/browse/./No-Intro/Nintendo - Nintendo 3DS (Digital) (CDN)/", paths)
        self.assertTrue(all("Nintendo 64" not in p for p in paths))

    def test_find_companions_same_folder_and_latest_update(self):
        current = [
            {"name": "Game (USA).zip", "href": "/rom?id=1", "size": "1 GB", "is_folder": False},
            {"name": "Game (USA) (DLC).zip", "href": "/rom?id=2", "size": "200 MB", "is_folder": False},
            {"name": "Game (USA) (v1.01) (Update).zip", "href": "/rom?id=3", "size": "10 MB", "is_folder": False},
            {"name": "Game (USA) (v1.04) (Update).zip", "href": "/rom?id=4", "size": "12 MB", "is_folder": False},
            {"name": "Other Game (USA) (DLC).zip", "href": "/rom?id=5", "size": "8 MB", "is_folder": False},
        ]
        parent = [
            {"name": "Nintendo - Nintendo 3DS (Digital) (CDN)/",
             "href": "/browse/./No-Intro/Nintendo - Nintendo 3DS (Digital) (CDN)/",
             "is_folder": True},
        ]

        def fetch(path):
            if "Digital" in path:
                return current
            return parent

        found = find_companions(
            "Game (USA).zip",
            "/browse/./No-Intro/Nintendo - Nintendo 3DS (Digital) (CDN)/",
            current_entries=current,
            fetch_fn=fetch,
            latest_update_only=True,
        )
        names = [c.name for c in found]
        self.assertIn("Game (USA) (DLC).zip", names)
        self.assertIn("Game (USA) (v1.04) (Update).zip", names)
        self.assertNotIn("Game (USA) (v1.01) (Update).zip", names)
        self.assertNotIn("Other Game (USA) (DLC).zip", names)

    def test_version_key_orders_updates(self):
        self.assertGreater(version_key("Game (v1.04) (Update).zip"), version_key("Game (v1.01) (Update).zip"))
        self.assertGreater(version_key("Game [v196608].nsp"), version_key("Game [v65536].nsp"))

    def test_skip_when_queued_file_is_dlc(self):
        found = find_companions(
            "Game (USA) (DLC).zip",
            "/browse/./No-Intro/Foo/",
            current_entries=[],
            fetch_fn=lambda p: [],
        )
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
