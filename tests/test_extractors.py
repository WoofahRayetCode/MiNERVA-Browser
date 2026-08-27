import unittest
import pathlib
import tempfile
import zipfile
import tarfile
import gzip
import bz2
import lzma
from minerva.core.extractors import (
    normalize_chd_stem,
    clean_chd_names_in_base,
    is_likely_rom_file,
    chd_source_mode,
    collect_chd_sources,
    format_extractor_status,
    extract_archive,
)


class TestExtractors(unittest.TestCase):
    def test_normalize_chd_stem(self):
        # Removes region tags, keeps disc numbers
        self.assertEqual(
            normalize_chd_stem("Final Fantasy VII (USA) (Disc 1)"),
            "Final Fantasy VII (Disc 1)",
        )
        self.assertEqual(
            normalize_chd_stem("Crash Bandicoot (Europe) (En,Fr,De)"),
            "Crash Bandicoot",
        )
        self.assertEqual(
            normalize_chd_stem("Metal Gear Solid (Japan) (Rev 1) (Disc 2)"),
            "Metal Gear Solid (Disc 2)",
        )

    def test_is_likely_rom_file(self):
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.chd")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.iso")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("track.bin")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("disc.gdi")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.cdi")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.rvz")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.wbfs")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.cso")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("game.smd")))
        self.assertFalse(is_likely_rom_file(pathlib.Path("readme.txt")))
        self.assertFalse(is_likely_rom_file(pathlib.Path("cover.jpg")))

    def test_chd_source_mode(self):
        self.assertEqual(chd_source_mode(pathlib.Path("game.cue")), "createcd")
        self.assertEqual(chd_source_mode(pathlib.Path("disc.gdi")), "createcd")
        self.assertEqual(chd_source_mode(pathlib.Path("disc.ccd")), "createcd")
        self.assertEqual(chd_source_mode(pathlib.Path("disc.toc")), "createcd")
        self.assertEqual(chd_source_mode(pathlib.Path("disc.nrg")), "createcd")
        self.assertEqual(chd_source_mode(pathlib.Path("game.iso")), "createdvd")
        self.assertEqual(chd_source_mode(pathlib.Path("game.mds")), "createdvd")
        self.assertIsNone(chd_source_mode(pathlib.Path("game.txt")))

    def test_collect_chd_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            (base / "game1.cue").write_text("dummy cue")
            (base / "game2.gdi").write_text("dummy gdi")
            (base / "game3.iso").write_text("dummy iso")
            (base / "notes.txt").write_text("dummy text")

            sources = collect_chd_sources(base)
            names = [s.name for s in sources]
            self.assertIn("game1.cue", names)
            self.assertIn("game2.gdi", names)
            self.assertIn("game3.iso", names)
            self.assertNotIn("notes.txt", names)

    def test_clean_chd_names_in_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            game_file = base / "Ridge Racer (USA).chd"
            game_file.write_text("dummy")
            dreamcast_file = base / "Sonic Adventure (USA) (En,Ja).gdi"
            dreamcast_file.write_text("dummy")

            renamed, unchanged, failed = clean_chd_names_in_base(base)
            self.assertEqual(renamed, 2)
            self.assertEqual(unchanged, 0)
            self.assertEqual(len(failed), 0)
            self.assertTrue((base / "Ridge Racer.chd").exists())
            self.assertTrue((base / "Sonic Adventure.gdi").exists())

    def test_format_extractor_status(self):
        self.assertIn("built-in Python extraction", format_extractor_status([]))
        tools = [{"label": "7-Zip", "exe": "/usr/bin/7z"}]
        self.assertIn("7-Zip: /usr/bin/7z", format_extractor_status(tools))

    def test_extract_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            zip_path = base / "game.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("game.nes", "NES ROM DATA")

            out_dir = base / "out_zip"
            ok = extract_archive(zip_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.nes").exists())
            self.assertEqual((out_dir / "game.nes").read_text(), "NES ROM DATA")

    def test_extract_tar_gz(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            tar_path = base / "roms.tar.gz"
            temp_rom = base / "game.sfc"
            temp_rom.write_text("SNES ROM DATA")

            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(temp_rom, arcname="game.sfc")

            out_dir = base / "out_tar"
            ok = extract_archive(tar_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.sfc").exists())
            self.assertEqual((out_dir / "game.sfc").read_text(), "SNES ROM DATA")

    def test_extract_gzip_single_rom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            gz_path = base / "game.gba.gz"
            with gzip.open(gz_path, "wb") as f_out:
                f_out.write(b"GBA ROM DATA")

            out_dir = base / "out_gz"
            ok = extract_archive(gz_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.gba").exists())
            self.assertEqual((out_dir / "game.gba").read_bytes(), b"GBA ROM DATA")

    def test_extract_bz2_single_rom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            bz2_path = base / "game.z64.bz2"
            with bz2.open(bz2_path, "wb") as f_out:
                f_out.write(b"N64 ROM DATA")

            out_dir = base / "out_bz2"
            ok = extract_archive(bz2_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.z64").exists())
            self.assertEqual((out_dir / "game.z64").read_bytes(), b"N64 ROM DATA")

    def test_extract_xz_single_rom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            xz_path = base / "game.nds.xz"
            with lzma.open(xz_path, "wb") as f_out:
                f_out.write(b"NDS ROM DATA")

            out_dir = base / "out_xz"
            ok = extract_archive(xz_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.nds").exists())
            self.assertEqual((out_dir / "game.nds").read_bytes(), b"NDS ROM DATA")

    def test_extract_passthrough(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            rom_path = base / "game.chd"
            rom_path.write_bytes(b"MAME CHD DATA")

            out_dir = base / "out_pass"
            ok = extract_archive(rom_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.chd").exists())
            self.assertEqual((out_dir / "game.chd").read_bytes(), b"MAME CHD DATA")


if __name__ == "__main__":
    unittest.main()
