import pathlib
import tempfile
import unittest
from unittest import mock
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


class TestPlaceDkeys(unittest.TestCase):
    KEY = "E40B60CEFFF899F952C1B35C58102355"

    def setUp(self):
        ps3_dkeys.reset_dkey_catalog_cache()
        patcher = mock.patch.object(ps3_dkeys, "fetch_entries", return_value=[])  # never touch the network
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(ps3_dkeys.reset_dkey_catalog_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = pathlib.Path(self._tmp.name)
        (self.base / "dkeys").mkdir()
        (self.base / "extracted").mkdir()

    def _iso(self, name="Persona 5 (USA).iso"):
        iso = self.base / "extracted" / name
        iso.write_bytes(b"\0" * 16)
        return iso

    def _key(self, stem="Persona 5 (USA)", text=None):
        key = self.base / "dkeys" / f"{stem}.dkey"
        key.write_text(text or self.KEY)
        return key

    def test_copies_the_key_next_to_the_iso_under_the_iso_name(self):
        iso, key = self._iso(), self._key()
        counts = ps3_dkeys.place_dkeys_beside_isos(self.base)
        self.assertEqual(counts["placed"], 1)
        self.assertEqual(iso.with_suffix(".dkey").read_text(), self.KEY)
        self.assertTrue(key.is_file())  # the original stays in dkeys/

    def test_is_idempotent_and_never_overwrites_a_valid_existing_key(self):
        iso, _ = self._iso(), self._key()
        mine = iso.with_suffix(".dkey")
        mine.write_text("0" * 32)  # a valid-looking key the user put there
        counts = ps3_dkeys.place_dkeys_beside_isos(self.base)
        self.assertEqual((counts["placed"], counts["present"]), (0, 1))
        self.assertEqual(mine.read_text(), "0" * 32)

    def test_replaces_a_corrupt_sidecar_key(self):
        iso, _ = self._iso(), self._key()
        iso.with_suffix(".dkey").write_text("not a key")
        self.assertEqual(ps3_dkeys.place_dkeys_beside_isos(self.base)["placed"], 1)
        self.assertEqual(iso.with_suffix(".dkey").read_text(), self.KEY)

    def test_a_key_for_a_different_region_is_not_placed(self):
        # Title-only matching would pair these (region tags are stripped); the region check stops it.
        iso = self._iso("Persona 5 (Europe).iso")
        self._key(stem="Persona 5 (USA)")
        counts = ps3_dkeys.place_dkeys_beside_isos(self.base)
        self.assertEqual((counts["placed"], counts["mismatch"]), (0, 1))
        self.assertFalse(iso.with_suffix(".dkey").exists())

    def test_title_only_fallback_finds_a_key_in_the_overlapping_dkeys_folder(self):
        iso = self._iso("Persona 5 (USA) (En,Fr).iso")
        self._key(stem="Persona 5 (USA)")  # same title, not the same name
        counts = ps3_dkeys.place_dkeys_beside_isos(self.base)
        self.assertEqual(counts["placed"], 1)
        self.assertTrue(iso.with_suffix(".dkey").is_file())

    def test_iso_without_a_matching_key_is_reported_not_guessed(self):
        self._iso("Some Other Game (USA).iso")
        self._key(stem="Persona 5 (USA)")
        counts = ps3_dkeys.place_dkeys_beside_isos(self.base)
        self.assertEqual((counts["placed"], counts["no_key"]), (0, 1))
        self.assertFalse(list(self.base.rglob("Some Other Game*.dkey")))

    def test_ignores_isos_inside_the_dkeys_folder_and_missing_directories(self):
        (self.base / "dkeys" / "stray.iso").write_bytes(b"x")
        self.assertEqual(sum(ps3_dkeys.place_dkeys_beside_isos(self.base).values()), 0)
        self.assertEqual(ps3_dkeys.place_dkeys_beside_isos(self.base / "nope"),
                         {"placed": 0, "present": 0, "no_key": 0, "mismatch": 0})


class TestKeyMatchesIso(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def iso(self, name, header=b""):
        p = self.dir / name
        p.write_bytes(header + b"\0" * 64)
        return p

    def test_exact_same_name_matches(self):
        self.assertEqual(ps3_dkeys.key_matches_iso(self.iso("Game (USA).iso"), self.dir / "Game (USA).dkey"),
                         ps3_dkeys.KEY_MATCH)

    def test_a_key_for_another_region_is_rejected(self):
        self.assertEqual(ps3_dkeys.key_matches_iso(self.iso("Game (Europe).iso"), self.dir / "Game (USA).dkey"),
                         ps3_dkeys.KEY_MISMATCH)

    def test_overlapping_regions_are_not_a_mismatch(self):
        verdict = ps3_dkeys.key_matches_iso(self.iso("Game (USA, Europe).iso"), self.dir / "Game (USA).dkey")
        self.assertNotEqual(verdict, ps3_dkeys.KEY_MISMATCH)

    def test_serial_in_key_name_is_checked_against_the_iso_header(self):
        header = b"PlayStation3\0\0TITLE_ID\0BLUS30808\0"
        iso = self.iso("Game (USA).iso", header)
        self.assertEqual(ps3_dkeys.key_matches_iso(iso, self.dir / "Game (USA) (BLUS-30808).dkey"), ps3_dkeys.KEY_MATCH)
        self.assertEqual(ps3_dkeys.key_matches_iso(iso, self.dir / "Game (USA) (BLUS-30999).dkey"), ps3_dkeys.KEY_MISMATCH)

    def test_serials_in_both_names_must_agree(self):
        iso = self.iso("Game (BLES-01234).iso")
        self.assertEqual(ps3_dkeys.key_matches_iso(iso, self.dir / "Game (BLUS-30808).dkey"), ps3_dkeys.KEY_MISMATCH)

    def test_not_enough_information_is_unknown_not_a_guess(self):
        self.assertEqual(ps3_dkeys.key_matches_iso(self.iso("Some Game.iso"), self.dir / "Some Game Edition.dkey"),
                         ps3_dkeys.KEY_UNKNOWN)


class TestKeySelection(unittest.TestCase):
    """Catalog shapes taken from the real Disc Keys TXT folder."""

    def setUp(self):
        ps3_dkeys.reset_dkey_catalog_cache()
        ps3_dkeys.pop_ambiguous()
        self.addCleanup(ps3_dkeys.reset_dkey_catalog_cache)

    def catalog(self, *names):
        entries = [{"name": n, "href": f"/rom?id={i}", "is_folder": False} for i, n in enumerate(names, 1)]
        ps3_dkeys._cache_entries = entries
        ps3_dkeys._cache_index = ps3_dkeys._build_index(entries)
        ps3_dkeys._cache_fetched_at = 10 ** 12
        return entries

    def pick(self, rom, **kw):
        return ps3_dkeys.resolve_dkey_entry(rom, **kw)

    def test_exact_name_always_wins(self):
        self.catalog("Assassin's Creed III (USA) (v01.01).zip", "Assassin's Creed III (USA) (v02.00).zip")
        r = self.pick("Assassin's Creed III (USA) (v02.00).iso")
        self.assertEqual(r.entry["name"], "Assassin's Creed III (USA) (v02.00).zip")

    def test_version_in_the_rom_name_selects_the_matching_revision(self):
        self.catalog("Game X (USA) (v01.01).zip", "Game X (USA) (v02.00).zip")
        r = self.pick("Game X (USA) (En,Fr) (v02.00).iso")
        self.assertEqual(r.entry["name"], "Game X (USA) (v02.00).zip")

    def test_two_revisions_and_no_evidence_is_ambiguous_not_a_guess(self):
        self.catalog("Game X (USA) (v01.01).zip", "Game X (USA) (v02.00).zip")
        r = self.pick("Game X (USA) (En,Fr).iso")
        self.assertIsNone(r.entry)
        self.assertEqual(sorted(r.ambiguous), ["Game X (USA) (v01.01).zip", "Game X (USA) (v02.00).zip"])
        self.assertIsNone(ps3_dkeys.find_dkey_entry("Game X (USA) (En,Fr).iso"))
        self.assertIn("Game X (USA) (En,Fr).iso", ps3_dkeys.pop_ambiguous())
        self.assertEqual(ps3_dkeys.pop_ambiguous(), {})

    def test_embedded_title_id_selects_between_serial_variants(self):
        self.catalog("Revelations (USA) (BLUS-30808).zip", "Revelations (USA) (BLUS-30905).zip")
        r = self.pick("Revelations (USA).iso", title_ids=["BLUS30905"])
        self.assertEqual(r.entry["name"], "Revelations (USA) (BLUS-30905).zip")

    def test_entries_for_another_region_are_never_candidates(self):
        self.catalog("Game Y (Europe).zip")
        self.assertIsNone(self.pick("Game Y (USA).iso").entry)

    def test_spelling_variants_of_one_release_count_once(self):
        self.catalog("Ar tonelico III (Japan).zip", "Ar Tonelico III (Japan).zip", "AKB1-149 (Japan).zip", "AKB1 149 (Japan).zip")
        self.assertIsNotNone(self.pick("Ar Tonelico III (Japan) (Disc).iso").entry or self.pick("Ar Tonelico III (Japan).iso").entry)
        r = self.pick("AKB1 149 (Japan) (En).iso")
        self.assertIsNotNone(r.entry)
        self.assertEqual(r.ambiguous, ())

    def test_plain_edition_is_preferred_but_best_edition_is_chosen_when_named(self):
        self.catalog("Armored Core (Japan).zip", "Armored Core (Japan) (PlayStation 3 the Best).zip")
        plain = self.pick("Armored Core (Japan) (En).iso")
        self.assertEqual(plain.entry["name"], "Armored Core (Japan).zip")
        best = self.pick("Armored Core (Japan) (PlayStation 3 the Best) (En).iso")
        self.assertEqual(best.entry["name"], "Armored Core (Japan) (PlayStation 3 the Best).zip")

    def test_a_single_plausible_entry_is_still_returned(self):
        self.catalog("Unique Game (USA) (En,Fr).zip")
        self.assertEqual(self.pick("Unique Game (USA).iso").entry["name"], "Unique Game (USA) (En,Fr).zip")


if __name__ == "__main__":
    unittest.main()
