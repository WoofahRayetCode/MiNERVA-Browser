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

    def parse(self, html):
        parser = EntryParser()
        parser.feed(html)
        return parser.entries

    def test_names_get_their_real_capitalisation_from_the_link_text(self):
        # Shape of the archive's listing: data-name is lower-cased, the link text is not.
        entries = self.parse("""
            <div class="entry" data-name="&#39;&amp;&#39; - sora no mukou de sakimasu you ni (japan).zip">
                <a href="/rom?id=1163718" draggable="false">&#39;&amp;&#39; - Sora no Mukou de Sakimasu You ni (Japan).zip</a>
                <span>4.20 GB</span>
                <a href="javascript:void(0)" onclick="downloadMagnet('magnet:?xt=urn:btih:626a&amp;dn=Minerva_Myrient')" title="Download Magnet">&#129522;</a>
            </div>
            <div class="entry" data-name="infamous (usa).iso">
                <a href="/rom?id=1162297" draggable="false">inFamous (USA).iso</a>
                <span>13.59 GB</span>
            </div>
        """)
        self.assertEqual([e["name"] for e in entries], [
            "'&' - Sora no Mukou de Sakimasu You ni (Japan).zip",
            "inFamous (USA).iso",
        ])
        self.assertEqual(entries[1]["href"], "/rom?id=1162297")
        self.assertEqual(entries[1]["size"], "13.59 GB")  # the link text never leaks into the size

    def test_folder_names_get_capitalisation_without_the_trailing_slash(self):
        entries = self.parse("""
            <div class="entry" data-name="acorn - archimedes">
                <a href="/browse/./Redump/Acorn%20-%20Archimedes/" draggable="false"><strong>Acorn - Archimedes/</strong></a>
                <span>21.21 GB</span>
            </div>
        """)
        self.assertEqual(entries[0]["name"], "Acorn - Archimedes")
        self.assertTrue(entries[0]["is_folder"])

    def test_link_text_that_is_a_different_name_never_replaces_data_name(self):
        entries = self.parse("""
            <div class="entry" data-name="game (usa).iso"><a href="/rom?id=1">Download</a><span>1 MB</span></div>
            <div class="entry" data-name="game (usa).iso"><a href="/rom?id=2">Redump/Sony/Game (USA).iso</a></div>
            <div class="entry" data-name="game (usa).iso"><a href="/rom?id=3"></a></div>
        """)
        self.assertEqual([e["name"] for e in entries], ["game (usa).iso"] * 3)

    def test_an_entry_without_data_name_falls_back_to_the_href(self):
        entries = self.parse('<div class="entry"><a href="/browse/Redump/Some%20Game/">Some Game/</a></div>')
        self.assertEqual(entries[0]["name"], "Some Game")

    def test_state_does_not_leak_between_entries(self):
        entries = self.parse("""
            <div class="entry" data-name="a (usa).iso"><a href="/rom?id=1">A (USA).iso</a></div>
            <div class="entry" data-name="b (usa).iso"><a href="/rom?id=2">Download</a></div>
            <div class="entry" data-name="c (usa).iso"><a href="/rom?id=3">C (USA).iso</a></div>
        """)
        self.assertEqual([e["name"] for e in entries], ["A (USA).iso", "b (usa).iso", "C (USA).iso"])

    def test_extract_rom_id(self):
        self.assertEqual(extract_rom_id("/rom?id=98765"), "98765")
        self.assertEqual(extract_rom_id("/rom?foo=bar&id=42"), "42")
        self.assertIsNone(extract_rom_id("/browse/SNES/"))
        self.assertIsNone(extract_rom_id(""))


if __name__ == "__main__":
    unittest.main()
