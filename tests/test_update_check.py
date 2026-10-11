import time
import unittest
import urllib.parse

from minerva.core import companions as c
from minerva.core.update_check import check_library_updates

REDUMP = "/browse/./Redump/"
NOINTRO = "/browse/./No-Intro/"
PS3_BASE = REDUMP + "Sony%20-%20PlayStation%203/"
X360_BASE = REDUMP + "Microsoft%20-%20Xbox%20360/"
PS3_UPD = NOINTRO + "Sony%20-%20PlayStation%203%20(PSN)%20(Updates)/"
PS3_DLC = NOINTRO + "Sony%20-%20PlayStation%203%20(PSN)%20(DLC)/"
PS3_AVATARS = NOINTRO + "Sony%20-%20PlayStation%203%20(PSN)%20(Avatars)/"
X360_UPD = NOINTRO + "Unofficial%20-%20Microsoft%20-%20Xbox%20360%20(Title%20Updates)/"


def folder(root, name):
    return {"name": name, "href": root + urllib.parse.quote(name) + "/", "is_folder": True, "size": ""}


def file(path, name, size="1 MB"):
    return {"name": name, "href": f"/rom?id={abs(hash(path + name)) % 10**7}", "is_folder": False, "size": size}


class Archive:
    """A fake archive laid out like the real one (updates live in another collection)."""

    def __init__(self):
        self.calls: list[str] = []
        self.listings = {
            REDUMP: [folder(REDUMP, "Sony - PlayStation 3"), folder(REDUMP, "Microsoft - Xbox 360"),
                     folder(REDUMP, "Sony - PlayStation 3 - Disc Keys")],
            NOINTRO: [
                folder(NOINTRO, "Sony - PlayStation 3 (PSN) (Updates)"),
                folder(NOINTRO, "Sony - PlayStation 3 (PSN) (DLC)"),
                folder(NOINTRO, "Sony - PlayStation 3 (PSN) (Avatars)"),
                folder(NOINTRO, "Unofficial - Microsoft - Xbox 360 (Title Updates)"),
                folder(NOINTRO, "Microsoft - Xbox 360 (Digital)"),
                folder(NOINTRO, "Nintendo - Game Boy"),
            ],
            PS3_BASE: [file(PS3_BASE, "Demon's Souls (USA) (En,Fr,Es).zip")],
            X360_BASE: [file(X360_BASE, "Halo 4 (Europe) (En,Fr,De).zip")],
            PS3_UPD: [
                file(PS3_UPD, "Demon's Souls (USA) (v1.01) (Update).zip"),
                file(PS3_UPD, "Demon's Souls (USA) (v1.04) (Update).zip"),
                file(PS3_UPD, "Demon's Souls (Japan) (v1.09) (Update).zip"),
                file(PS3_UPD, "Another Game (USA) (v1.00) (Update).zip"),
            ],
            PS3_DLC: [file(PS3_DLC, "Demon's Souls (USA) (DLC).zip")],
            PS3_AVATARS: [file(PS3_AVATARS, "Demon's Souls Avatar (USA).zip")],
            X360_UPD: [
                file(X360_UPD, "Halo 4 (World) (v9) (1F887266) (Title Update).zip"),
                file(X360_UPD, "Halo 4 (World) (v10) (1F887267) (Title Update).zip"),
                file(X360_UPD, "Halo 3 (World) (v3).zip"),
                file(X360_UPD, "Halo 4 (Japan) (v12).zip"),
            ],
        }

    @staticmethod
    def _key(path):
        return urllib.parse.unquote(path).rstrip("/").lower()

    def __call__(self, path):
        self.calls.append(self._key(path))
        by_key = {self._key(k): v for k, v in self.listings.items()}
        return list(by_key[self._key(path)])


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        c.reset_companion_cache()

    def test_update_folders_in_other_collections_are_found(self):
        paths = c.discover_companion_paths(PS3_BASE, Archive())
        self.assertEqual(
            sorted(urllib.parse.unquote(p) for p in paths),
            sorted(urllib.parse.unquote(p) for p in (PS3_BASE, PS3_UPD, PS3_DLC)),
        )  # Avatars, Disc Keys and other systems are not companion sets

    def test_unofficial_prefix_and_title_updates_folder_map_to_the_base_system(self):
        paths = c.discover_companion_paths(X360_BASE, Archive())
        decoded = [urllib.parse.unquote(p) for p in paths]
        self.assertIn(urllib.parse.unquote(X360_UPD), decoded)
        self.assertEqual(c.system_key("Unofficial - Microsoft - Xbox 360 (Title Updates)"), c.system_key("Microsoft - Xbox 360"))

    def test_ps3_game_gets_its_latest_update_and_dlc_from_the_psn_folders(self):
        found = c.find_companions("Demon's Souls (USA) (En,Fr,Es).zip", PS3_BASE, fetch_fn=Archive())
        names = [f.name for f in found]
        self.assertEqual(names, ["Demon's Souls (USA) (v1.04) (Update).zip", "Demon's Souls (USA) (DLC).zip"])

    def test_xbox360_title_update_marked_world_applies_to_a_european_disc(self):
        found = c.find_companions("Halo 4 (Europe) (En,Fr,De).zip", X360_BASE, fetch_fn=Archive())
        self.assertEqual([f.name for f in found], ["Halo 4 (World) (v10) (1F887267) (Title Update).zip"])

    def test_a_different_game_or_region_is_not_offered(self):
        found = c.find_companions("Halo 4 (Europe) (En,Fr,De).zip", X360_BASE, fetch_fn=Archive())
        self.assertFalse(any("Halo 3" in f.name or "Japan" in f.name for f in found))

    def test_listing_requests_are_cached_across_games(self):
        archive = Archive()
        for _ in range(5):
            c.find_companions("Demon's Souls (USA) (En,Fr,Es).zip", PS3_BASE, fetch_fn=archive)
        self.assertEqual(len(archive.calls), len(set(archive.calls)))  # each folder fetched once


class TestFlagsAndRegions(unittest.TestCase):
    def test_title_update_flag_and_hex_ids(self):
        self.assertEqual(c.classify_release("Halo 4 (World) (v10) (1F887267) (Title Update).zip"), c.KIND_UPDATE)
        self.assertEqual(
            c.title_key("Halo 4 (World) (v10) (1F887267) (Title Update).zip"),
            c.title_key("Halo 4 (Europe) (En,Fr,De).zip"),
        )

    def test_world_is_compatible_with_every_region(self):
        self.assertTrue(c.regions_compatible("Game (USA).zip", "Game (World).zip"))
        self.assertTrue(c.regions_compatible("Game (World).zip", "Game (Japan).zip"))
        self.assertFalse(c.regions_compatible("Game (USA).zip", "Game (Japan).zip"))
        self.assertTrue(c.regions_compatible("Game.zip", "Game (Japan).zip"))  # unknown region: do not exclude


class TestIndexScale(unittest.TestCase):
    def setUp(self):
        c.reset_companion_cache()

    def test_a_20k_entry_folder_is_indexed_once_and_answers_quickly(self):
        entries = [file("big", f"Title {i} (World) (v{i % 9 + 1}) (Title Update).zip") for i in range(20_000)]
        start = time.perf_counter()
        index = c.CompanionIndex.build(entries, "Unofficial - Microsoft - Xbox 360 (Title Updates)")
        build = time.perf_counter() - start
        self.assertEqual(index.size, 20_000)
        start = time.perf_counter()
        hits = sum(len(index.candidates(f"Title {i} (Europe).zip")) for i in range(0, 20_000, 40))
        lookups = time.perf_counter() - start
        self.assertEqual(hits, 500)
        self.assertLess(build, 30.0, f"index build took {build:.1f}s")
        self.assertLess(lookups, 4.0, f"500 lookups took {lookups:.2f}s")


class TestLibraryCheck(unittest.TestCase):
    def setUp(self):
        c.reset_companion_cache()
        self.archive = Archive()
        self.games = [
            {"name": "Demon's Souls (USA) (En,Fr,Es).zip", "browse_path": PS3_BASE},
            {"name": "Halo 4 (Europe) (En,Fr,De).zip", "browse_path": X360_BASE},
        ]

    def run_check(self, installed=(), games=None, **kw):
        return check_library_updates(games or self.games, installed, self.archive, **kw)

    def test_offers_updates_for_every_downloaded_game(self):
        result = self.run_check()
        names = sorted(i.name for i in result.items)
        self.assertEqual(names, sorted([
            "Demon's Souls (USA) (v1.04) (Update).zip",
            "Demon's Souls (USA) (DLC).zip",
            "Halo 4 (World) (v10) (1F887267) (Title Update).zip",
        ]))
        self.assertEqual((result.checked, result.unknown_folder, result.errors), (2, 0, []))

    def test_already_downloaded_files_are_not_offered_again(self):
        result = self.run_check(installed={"Demon's Souls (USA) (v1.04) (Update).zip", "demon's souls (usa) (dlc).zip"})
        self.assertEqual([r.base_name for r in result.reports], ["Halo 4 (Europe) (En,Fr,De).zip"])

    def test_an_older_update_than_the_installed_one_is_not_offered(self):
        installed = {"Halo 4 (World) (v10) (1F887267) (Title Update).zip"}
        result = self.run_check(installed=installed)
        self.assertNotIn("Halo 4 (Europe) (En,Fr,De).zip", [r.base_name for r in result.reports])

    def test_a_newer_update_than_the_installed_one_is_offered(self):
        installed = {"Demon's Souls (USA) (v1.01) (Update).zip"}
        result = self.run_check(installed=installed)
        report = next(r for r in result.reports if r.base_name.startswith("Demon"))
        self.assertIn("Demon's Souls (USA) (v1.04) (Update).zip", [i.name for i in report.items])

    def test_games_without_a_known_archive_folder_are_counted_not_guessed(self):
        games = self.games + [{"name": "Old Download (USA).zip", "browse_path": ""}]
        result = self.run_check(games=games)
        self.assertEqual(result.unknown_folder, 1)
        self.assertEqual(result.checked, 2)

    def test_update_and_dlc_files_in_the_history_are_not_treated_as_games(self):
        games = self.games + [{"name": "Demon's Souls (USA) (v1.04) (Update).zip", "browse_path": ""}]
        result = self.run_check(games=games)
        self.assertEqual((result.checked, result.unknown_folder), (2, 0))

    def test_a_failing_lookup_is_reported_and_does_not_stop_the_rest(self):
        def finder(name, path, **kw):
            if name.startswith("Halo"):
                raise RuntimeError("archive down")
            return c.find_companions(name, path, **kw)

        result = self.run_check(finder=finder)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.reports), 1)

    def test_cancel_stops_early_and_progress_is_reported(self):
        seen = []
        result = self.run_check(progress=lambda i, n, name: seen.append((i, n)), cancel=lambda: len(seen) >= 1)
        self.assertTrue(result.cancelled)
        self.assertEqual(seen, [(1, 2)])


if __name__ == "__main__":
    unittest.main()
