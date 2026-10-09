import unittest

from minerva.core.entries import detect_regions, detect_release_tags, enrich_entries, parse_size_bytes
from minerva.core.extractors import library_keys_for_name


class TestParseSize(unittest.TestCase):
    def test_units_and_garbage(self):
        self.assertEqual(parse_size_bytes("1.5 GB"), int(1.5 * 1024 ** 3))
        self.assertEqual(parse_size_bytes("250 MB"), 250 * 1024 ** 2)
        self.assertEqual(parse_size_bytes("12 KiB"), 12 * 1024)
        self.assertEqual(parse_size_bytes("512"), 512)
        for bad in ("", "  ", None, "big", "1.2.3 GB", 5):
            self.assertEqual(parse_size_bytes(bad), 0, bad)


class TestDetectors(unittest.TestCase):
    def test_release_tags(self):
        self.assertEqual(detect_release_tags("game (usa) (demo).zip"), {"demo"})
        self.assertEqual(detect_release_tags("game (beta 2) (proto).zip"), {"beta", "proto"})
        self.assertEqual(detect_release_tags("game (rev a) (unl).zip"), {"revision", "unlicensed"})
        self.assertEqual(detect_release_tags("game (usa).zip"), set())
        self.assertEqual(detect_release_tags("game [hack].zip"), {"hack"})
        self.assertEqual(detect_release_tags("game (t+eng v1.0 someone).zip"), {"translation"})
        self.assertEqual(detect_release_tags("game (prototype 1992-01-01).zip"), {"proto"})

    def test_release_tags_do_not_fire_on_title_words(self):
        for name in ("demolition man (usa).zip", "the beta fish (usa).zip", "game (review copy).zip",
                     "protocol zero (usa).zip", "hacker (usa).zip"):
            self.assertEqual(detect_release_tags(name), set(), name)

    def test_unbracketed_hints_still_count_as_whole_words(self):
        self.assertEqual(detect_release_tags("game demo.zip"), {"demo"})
        self.assertEqual(detect_release_tags("game - beta.zip"), {"beta"})

    def test_regions(self):
        self.assertEqual(detect_regions("game (usa).zip"), {"usa"})
        self.assertEqual(detect_regions("game (japan) (en,ja).zip"), {"japan"})
        self.assertEqual(detect_regions("game (ue).zip"), {"usa", "europe"})
        self.assertEqual(detect_regions("game (u).zip"), {"usa"})
        self.assertEqual(detect_regions("game (hong kong).zip"), {"hong_kong"})
        self.assertEqual(detect_regions("untagged game.zip"), {"other"})
        self.assertEqual(detect_regions("game (world).zip"), {"world"})

    def test_multi_region_lists_are_fully_detected(self):
        # Redump/No-Intro naming: this used to yield only the first region.
        self.assertEqual(detect_regions("game (usa, europe).zip"), {"usa", "europe"})
        self.assertEqual(detect_regions("game (europe, usa).zip"), {"usa", "europe"})
        self.assertEqual(detect_regions("game (japan, usa, korea).zip"), {"japan", "usa", "korea"})
        self.assertEqual(detect_regions("game (usa/europe).zip"), {"usa", "europe"})
        self.assertEqual(detect_regions("game (usa, australia) (en,fr,de).zip"), {"usa", "australia"})

    def test_language_lists_and_title_words_are_not_regions(self):
        self.assertEqual(detect_regions("game (usa) (en,fr,de,es,it).zip"), {"usa"})
        self.assertEqual(detect_regions("game (europe) (fr,de).zip"), {"europe"})
        # '(de' / '(se' / '(au' / '(it' / '(br' prefixes used to match these:
        for name in ("game (demo).zip", "game (set 2).zip", "game (auto).zip",
                     "game (item pack).zip", "game (bravo).zip", "game (disc 1).zip"):
            self.assertEqual(detect_regions(name), {"other"}, name)

    def test_unbracketed_principal_regions(self):
        self.assertEqual(detect_regions("game - usa.zip"), {"usa"})
        self.assertEqual(detect_regions("game europe.zip"), {"europe"})
        self.assertEqual(detect_regions("world cup soccer.zip"), {"other"})  # title word, not a region


class TestEnrichEntries(unittest.TestCase):
    def test_adds_cached_fields_to_files_and_folders(self):
        entries = [
            {"name": "Game (USA) (Demo).zip", "href": "/rom?id=1", "size": "1 MB", "is_folder": False},
            {"name": "Sub Folder", "href": "/browse/x/", "size": "", "is_folder": True},
        ]
        out = enrich_entries(entries)
        self.assertIs(out, entries)
        file_entry, folder = entries
        self.assertEqual(file_entry["lname"], "game (usa) (demo).zip")
        self.assertEqual(file_entry["tags"], frozenset({"demo"}))
        self.assertEqual(file_entry["regions"], frozenset({"usa"}))  # "(Demo)" is not Germany
        self.assertEqual(file_entry["size_bytes"], 1024 ** 2)
        self.assertEqual(file_entry["keys"], library_keys_for_name("Game (USA) (Demo).zip"))
        self.assertEqual(folder["lname"], "sub folder")
        self.assertEqual((folder["tags"], folder["regions"], folder["keys"], folder["size_bytes"]),
                         (frozenset(), frozenset(), frozenset(), 0))

    def test_cached_fields_are_immutable(self):
        (entry,) = enrich_entries([{"name": "A (USA).zip", "href": "h", "size": "1 B", "is_folder": False}])
        for field in ("tags", "regions", "keys"):
            self.assertIsInstance(entry[field], frozenset)


if __name__ == "__main__":
    unittest.main()
