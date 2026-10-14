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
    detect_system_browse_path,
    KNOWN_SYSTEM_BROWSE_PATHS,
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

    def test_title_key_strips_media_flags(self):
        self.assertEqual(
            title_key("Demon's Souls (Japan) (v1.04) (Disc) (Update).zip"),
            title_key("Demon's Souls (Japan).zip"),
        )
        self.assertEqual(
            title_key("Battlefield - Bad Company 2 (USA) (v1.05) (Disc) (Update).zip"),
            title_key("Battlefield - Bad Company 2 (USA).zip"),
        )
        self.assertEqual(
            title_key("Game (USA) (Digital) (DLC).zip"),
            title_key("Game (USA).zip"),
        )

    def test_names_match_companion_rejects_beta_updates_for_stable_rom(self):
        # Stable base ROM should NOT match beta or prototype updates
        self.assertFalse(
            names_match_companion(
                "Battlefield - Bad Company 2 (USA).zip",
                "Battlefield - Bad Company 2 (USA) (v1.01) (Beta) (Update).zip",
            )
        )
        self.assertTrue(
            names_match_companion(
                "Battlefield - Bad Company 2 (USA).zip",
                "Battlefield - Bad Company 2 (USA) (v1.05) (Disc) (Update).zip",
            )
        )
        self.assertFalse(
            names_match_companion(
                "Resistance 3 (USA).zip",
                "Resistance 3 (USA) (v2.05) (Beta) (Update).zip",
            )
        )
        self.assertTrue(
            names_match_companion(
                "Resistance 3 (USA).zip",
                "Resistance 3 (USA) (v1.05) (Disc) (Update).zip",
            )
        )
        # Beta base ROM should match beta update
        self.assertTrue(
            names_match_companion(
                "Resistance 3 (USA) (Beta).zip",
                "Resistance 3 (USA) (v2.05) (Beta) (Update).zip",
            )
        )

    def test_find_companions_prefers_stable_disc_updates_over_beta(self):
        current = [
            {"name": "Battlefield - Bad Company 2 (USA).zip", "href": "/rom?id=1", "size": "5 GB", "is_folder": False},
        ]
        updates = [
            {"name": "Battlefield - Bad Company 2 (USA) (v1.01) (Beta) (Update).zip", "href": "/rom?id=2", "size": "8 MB", "is_folder": False},
            {"name": "Battlefield - Bad Company 2 (USA) (v1.05) (Disc) (Update).zip", "href": "/rom?id=3", "size": "200 MB", "is_folder": False},
        ]

        def fetch(path):
            if "Updates" in path:
                return updates
            return [
                {"name": "Sony - PlayStation 3 (PSN) (Updates)/", "href": "/browse/./No-Intro/Sony - PlayStation 3 (PSN) (Updates)/", "is_folder": True}
            ]

        found = find_companions(
            "Battlefield - Bad Company 2 (USA).zip",
            "/browse/./Redump/Sony - PlayStation 3/",
            current_entries=current,
            fetch_fn=fetch,
            latest_update_only=True,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "Battlefield - Bad Company 2 (USA) (v1.05) (Disc) (Update).zip")

    def test_skip_when_queued_file_is_dlc(self):
        found = find_companions(
            "Game (USA) (DLC).zip",
            "/browse/./No-Intro/Foo/",
            current_entries=[],
            fetch_fn=lambda p: [],
        )
        self.assertEqual(found, [])

    def test_detect_system_browse_path(self):
        # PS3 serials
        self.assertEqual(
            detect_system_browse_path("Demon's Souls [BCUS98174].iso"),
            KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 3"],
        )
        self.assertEqual(
            detect_system_browse_path("BLUS30444 - Metal Gear Solid 4.iso"),
            KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 3"],
        )
        # PS Vita serials and .vpk
        self.assertEqual(
            detect_system_browse_path("Persona 4 Golden [PCSE00120].iso"),
            KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Vita"],
        )
        self.assertEqual(
            detect_system_browse_path("HomebrewGame.vpk"),
            KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation Vita"],
        )
        # Switch Title ID and extensions
        self.assertEqual(
            detect_system_browse_path("Super Mario Odyssey [0100000000010000].nsp"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"],
        )
        self.assertEqual(
            detect_system_browse_path("Zelda BOTW.xci"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"],
        )
        # 3DS extensions
        self.assertEqual(
            detect_system_browse_path("Pokemon Sun.cia"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo 3DS"],
        )
        self.assertEqual(
            detect_system_browse_path("Pokemon Moon.3ds"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo 3DS"],
        )
        # Wii U extensions
        self.assertEqual(
            detect_system_browse_path("Mario Kart 8.wux"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Wii U"],
        )
        # Xbox 360
        self.assertEqual(
            detect_system_browse_path("Halo Reach.xiso.iso"),
            KNOWN_SYSTEM_BROWSE_PATHS["Microsoft - Xbox 360"],
        )
        # Path keywords
        self.assertEqual(
            detect_system_browse_path("/media/usb/ps3/Uncharted.iso"),
            KNOWN_SYSTEM_BROWSE_PATHS["Sony - PlayStation 3"],
        )
        self.assertEqual(
            detect_system_browse_path("/roms/switch/Metroid Dread.zip"),
            KNOWN_SYSTEM_BROWSE_PATHS["Nintendo - Nintendo Switch"],
        )
        # Unknown
        self.assertIsNone(detect_system_browse_path("Unknown_ROM_123.bin"))


if __name__ == "__main__":
    unittest.main()

