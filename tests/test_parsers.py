import unittest
from minerva.core.sqlite_http import EntryParser, extract_rom_id


class TestEntryParser(unittest.TestCase):
    def test_parse_entries_folder_and_file(self):
        html = """
        <div class="entry" data-name="Super Mario World (USA)">
            <a href="/rom?id=12345">Download</a>
            <span>3.5 MB</span>
            <a href="javascript:void(0)" class="magnet-btn">Magnet</a>
        </div>
        <div class="entry" data-name="SNES Games/">
            <a href="/browse/./SNES/">SNES Games</a>
            <span>12 items</span>
        </div>
        <div class="entry search_back">
            <a href="/browse/">Back</a>
        </div>
        """
        parser = EntryParser()
        parser.feed(html)
        entries = parser.entries

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "Super Mario World (USA)")
        self.assertEqual(entries[0]["href"], "/rom?id=12345")
        self.assertEqual(entries[0]["size"], "3.5 MB")
        self.assertFalse(entries[0]["is_folder"])

        self.assertEqual(entries[1]["name"], "SNES Games/")
        self.assertEqual(entries[1]["href"], "/browse/./SNES/")
        self.assertTrue(entries[1]["is_folder"])

    def test_extract_rom_id(self):
        self.assertEqual(extract_rom_id("/rom?id=98765"), "98765")
        self.assertEqual(extract_rom_id("/rom?foo=bar&id=42"), "42")
        self.assertIsNone(extract_rom_id("/browse/SNES/"))
        self.assertIsNone(extract_rom_id(""))


if __name__ == "__main__":
    unittest.main()
