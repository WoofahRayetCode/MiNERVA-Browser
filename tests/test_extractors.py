import unittest
import pathlib
import subprocess
import sys
import tempfile
import zipfile
from unittest import mock
import tarfile
import gzip
import bz2
import lzma
from minerva.core.extractors import (
    normalize_chd_stem,
    clean_chd_names_in_base,
    is_likely_rom_file,
    migrate_app_root_roms,
    is_archive_path,
    collect_downloaded_archives,
    collect_library_match_keys,
    library_status_for_name,
    verify_archive,
    ArchiveVerificationError,
    chd_source_mode,
    collect_chd_sources,
    format_extractor_status,
    extract_archive,
    format_bytes,
    display_filename,
    _parse_extractor_progress_line,
    should_convert_to_chd,
    chd_system_from_hints,
    classify_disc_image,
    is_likely_incorrect_chd,
    collect_incorrect_chds,
    repair_incorrect_chd,
    names_refer_to_same_rom,
    parse_chdman_info_media,
    classify_zip_disc,
    chd_companions_safe_to_delete,
    _hidden_subprocess_kwargs,
    classify_xbox_iso,
    classify_xbox_iso_from_reads,
    xbox_system_from_hints,
    should_unpack_xbox_iso,
    collect_xbox_iso_sources,
    xbox_unpack_command,
    pick_xdvdfs_release_asset,
    remove_xbox_system_update,
    unpack_xbox_iso,
    unpack_xbox_isos_in_dir,
    xbox_dump_percent,
    _parse_xbox_unpack_progress_line,
    XDVDFS_MAGIC,
)


class TestExtractors(unittest.TestCase):
    def test_normalize_chd_stem(self):
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
        self.assertEqual(
            normalize_chd_stem("Persona 5 (USA) (En,Fr) (Special Edition)"),
            "Persona 5 (Special Edition)",
        )
        self.assertEqual(
            normalize_chd_stem("Game (USA, Special Edition)"),
            "Game (Special Edition)",
        )
        self.assertEqual(
            normalize_chd_stem("Title (Europe) (Disc 2 of 2)"),
            "Title (Disc 2 of 2)",
        )
        self.assertEqual(
            normalize_chd_stem("Title (Japan) (Disk 1) (v01.00)"),
            "Title (Disk 1)",
        )
        self.assertEqual(
            normalize_chd_stem("BioShock (USA) (En,Fr,Es) (Limited Edition)"),
            "BioShock (Limited Edition)",
        )
        self.assertEqual(
            normalize_chd_stem("Collection (Europe) (Collector's Edition)"),
            "Collection (Collector's Edition)",
        )
        self.assertEqual(
            normalize_chd_stem("Game (World) [!] (Rev A)"),
            "Game",
        )
        self.assertEqual(
            normalize_chd_stem("Assassin's Creed (USA) (BLUS-30808)"),
            "Assassin's Creed (BLUS-30808)",
        )

    def test_migrate_app_root_roms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            dest = root / "downloads"
            (root / "Game (USA).zip").write_text("zip")
            (root / "Secret.nes").write_text("nes")
            (root / "minerva_settings.json").write_text("{}")
            extracted = root / "extracted" / "Game"
            extracted.mkdir(parents=True)
            (extracted / "Game.iso").write_text("iso")
            (root / "tools").mkdir()
            (root / "tools" / "chdman.exe").write_text("nope")

            moved, failed = migrate_app_root_roms(root, dest)
            self.assertGreaterEqual(moved, 3)
            self.assertEqual(failed, [])
            self.assertTrue((dest / "Game (USA).zip").exists())
            self.assertTrue((dest / "Secret.nes").exists())
            self.assertTrue((dest / "extracted" / "Game" / "Game.iso").exists())
            self.assertFalse((root / "Game (USA).zip").exists())
            self.assertTrue((root / "minerva_settings.json").exists())
            self.assertTrue((root / "tools" / "chdman.exe").exists())

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
        self.assertTrue(is_likely_rom_file(pathlib.Path("default.xex")))
        self.assertTrue(is_likely_rom_file(pathlib.Path("default.xbe")))
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
            ps2_iso = base / "Final Fantasy X.iso"
            ps2_iso.write_bytes(b"\x00" * 64 + b"SYSTEM.CNF\nBOOT2 = cdrom0:\\SLUS_123.45;1\n")
            psp_iso = base / "Persona.iso"
            psp_iso.write_bytes(b"\x00" * 64 + b"PSP_GAME\\PARAM.SFO\nUMD_DATA.BIN")

            sources = collect_chd_sources(base)
            names = [s.name for s in sources]
            self.assertIn("game1.cue", names)
            self.assertIn("game2.gdi", names)
            self.assertIn("Final Fantasy X.iso", names)
            self.assertNotIn("game3.iso", names)
            self.assertNotIn("Persona.iso", names)
            self.assertNotIn("notes.txt", names)

    def test_should_convert_to_chd_respects_system(self):
        self.assertEqual(chd_system_from_hints("/browse/Redump/Sony - PlayStation 2/"), "supported")
        self.assertEqual(chd_system_from_hints("/browse/Redump/Sony - PlayStation 3/"), "blocked")
        self.assertEqual(chd_system_from_hints("/browse/Redump/Sony - PlayStation Portable/"), "blocked")
        self.assertEqual(chd_system_from_hints("/browse/Redump/Sony - PlayStation/"), "supported")
        self.assertEqual(chd_system_from_hints("ULUS-12345 Game.iso"), "blocked")
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            cue = base / "Crash.cue"
            cue.write_text("FILE \"Crash.bin\" BINARY")
            self.assertTrue(should_convert_to_chd(cue))
            unknown_iso = base / "Mystery.iso"
            unknown_iso.write_bytes(b"not a known disc")
            self.assertFalse(should_convert_to_chd(unknown_iso))
            self.assertTrue(
                should_convert_to_chd(
                    unknown_iso,
                    context="/browse/Redump/Sony - PlayStation 2/",
                )
            )
            self.assertFalse(
                should_convert_to_chd(
                    unknown_iso,
                    context="/browse/Redump/Sony - PlayStation 3/",
                )
            )
            psp = base / "psp.iso"
            psp.write_bytes(b"PSP_GAME" + b"\x00" * 32)
            self.assertEqual(classify_disc_image(psp), "psp")
            self.assertFalse(should_convert_to_chd(psp, context="Sony - PlayStation 2"))

    def test_incorrect_chd_detection_and_restore(self):
        self.assertEqual(parse_chdman_info_media("Metadata: TAG='CHTR'  TRACK:1"), "cd")
        self.assertEqual(parse_chdman_info_media("Metadata: TAG='DVD '  LAYERS:1"), "dvd")
        self.assertTrue(names_refer_to_same_rom("Persona (USA).zip", "Persona (USA).chd"))
        self.assertFalse(names_refer_to_same_rom("Other Game.zip", "Persona.chd"))
        self.assertFalse(
            names_refer_to_same_rom("Grand Theft Auto (USA).zip", "Grand Theft Auto IV (USA).chd")
        )
        self.assertFalse(
            names_refer_to_same_rom("Assassin's Creed.zip", "Assassin's Creed II.chd")
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            extracted = pathlib.Path(tmpdir) / "extracted"
            extracted.mkdir()
            psp_dir = extracted / "PSP Game"
            psp_dir.mkdir()
            bad = psp_dir / "Game (USA) (ULUS-12345).chd"
            bad.write_bytes(b"MComprHD fake")
            self.assertTrue(is_likely_incorrect_chd(bad))
            ps2 = extracted / "FFX" / "Final Fantasy X (USA) (SLUS-20312).chd"
            ps2.parent.mkdir()
            ps2.write_bytes(b"MComprHD fake")
            self.assertFalse(is_likely_incorrect_chd(ps2))

            iso = psp_dir / "Game (USA) (ULUS-12345).iso"
            iso.write_bytes(b"PSP_GAME" + b"\x00" * 64)
            leftover = extracted / "FFX" / "Final Fantasy X (USA) (SLUS-20312).iso"
            leftover.write_bytes(b"SYSTEM.CNF\nBOOT2 = cdrom0:\\SLUS_203.12;1\n")
            self.assertTrue(chd_companions_safe_to_delete(ps2, context=str(extracted)))
            self.assertFalse(chd_companions_safe_to_delete(bad, context=str(extracted)))
            found = collect_incorrect_chds(extracted)
            self.assertIn(bad, found)
            self.assertNotIn(ps2, found)
            result = repair_incorrect_chd(bad)
            self.assertEqual(result["action"], "reversed")
            self.assertFalse(bad.exists())
            self.assertTrue(iso.exists())

            downloads = pathlib.Path(tmpdir)
            zip_path = downloads / "Monster Hunter (USA).zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("PSP_GAME/PARAM.SFO", "sfo")
                zf.writestr("game.iso", b"PSP_GAME" + b"\x00" * 32)
            self.assertEqual(classify_zip_disc(zip_path), "psp")
            mh_dir = extracted / "Monster Hunter (USA)"
            mh_dir.mkdir()
            mh_chd = mh_dir / "Monster Hunter (USA).chd"
            mh_chd.write_bytes(b"MComprHD fake")
            found2 = collect_incorrect_chds(extracted, download_dir=downloads)
            self.assertIn(mh_chd, found2)

    def test_clean_chd_names_in_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            game_file = base / "Ridge Racer (USA).chd"
            game_file.write_text("dummy")
            dreamcast_file = base / "Sonic Adventure (USA) (En,Ja).gdi"
            dreamcast_file.write_text("dummy")
            iso_file = base / "Persona 5 (USA) (Special Edition).iso"
            iso_file.write_text("dummy")
            dkey_file = base / "Persona 5 (USA) (Special Edition).dkey"
            dkey_file.write_text("dummy")

            renamed, unchanged, failed = clean_chd_names_in_base(base)
            self.assertEqual(renamed, 4)
            self.assertEqual(unchanged, 0)
            self.assertEqual(len(failed), 0)
            self.assertTrue((base / "Ridge Racer.chd").exists())
            self.assertTrue((base / "Sonic Adventure.gdi").exists())
            self.assertTrue((base / "Persona 5 (Special Edition).iso").exists())
            self.assertTrue((base / "Persona 5 (Special Edition).dkey").exists())

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

    def test_extract_zip_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            zip_path = base / "evil.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("game.nes", "NES ROM DATA")
                info = zipfile.ZipInfo("../evil.txt")
                zf.writestr(info, "hacked")
            out_dir = base / "out_zip"
            ok = extract_archive(zip_path, out_dir)
            self.assertTrue(ok)
            self.assertTrue((out_dir / "game.nes").exists())
            self.assertFalse((base / "evil.txt").exists())
            self.assertFalse(any(p.name == "evil.txt" for p in out_dir.rglob("*")))

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

    def test_verify_good_and_bad_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            good = base / "good.zip"
            with zipfile.ZipFile(good, "w") as zf:
                zf.writestr("game.nes", "NES ROM DATA")
            self.assertTrue(is_archive_path(good))
            self.assertEqual(verify_archive(good), "zip")

            empty = base / "empty.zip"
            empty.write_bytes(b"")
            with self.assertRaises(ArchiveVerificationError):
                verify_archive(empty)

            truncated = base / "bad.zip"
            truncated.write_bytes(good.read_bytes()[:20])
            with self.assertRaises(ArchiveVerificationError):
                verify_archive(truncated)

            corrupt = base / "corrupt.zip"
            data = bytearray(good.read_bytes())
            data[-8] = (data[-8] + 1) % 256
            corrupt.write_bytes(data)
            with self.assertRaises(ArchiveVerificationError):
                verify_archive(corrupt)

            out_dir = base / "out_bad"
            with self.assertRaises(ArchiveVerificationError):
                extract_archive(truncated, out_dir)
            extracted = list(out_dir.rglob("*")) if out_dir.exists() else []
            self.assertFalse(any(p.is_file() for p in extracted))

    def test_collect_downloaded_archives_skips_extracted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            keep = base / "Game (USA).zip"
            keep.write_bytes(b"PK\x03\x04")
            nested = base / "batch" / "other.7z"
            nested.parent.mkdir()
            nested.write_bytes(b"7z\xbc\xaf'\x1c")
            skipped = base / "extracted" / "Game.zip"
            skipped.parent.mkdir()
            skipped.write_bytes(b"PK\x03\x04")
            torrent_skip = base / "torrentfiles" / "pack.zip"
            torrent_skip.parent.mkdir()
            torrent_skip.write_bytes(b"PK\x03\x04")
            dkey_skip = base / "dkeys" / "Game (USA).zip"
            dkey_skip.parent.mkdir()
            dkey_skip.write_bytes(b"PK\x03\x04")
            names = [p.name for p in collect_downloaded_archives(base)]
            self.assertIn("Game (USA).zip", names)
            self.assertIn("other.7z", names)
            self.assertNotIn("Game.zip", names)
            self.assertNotIn("pack.zip", names)
            self.assertEqual(names.count("Game (USA).zip"), 1)
            excluded = collect_downloaded_archives(base, exclude_names={"Game (USA).zip"})
            excluded_names = [p.name for p in excluded]
            self.assertNotIn("Game (USA).zip", excluded_names)
            self.assertIn("other.7z", excluded_names)

    def test_library_status_queued_and_downloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            archive = base / "Chrono Trigger (USA).zip"
            archive.write_bytes(b"PK\x03\x04")
            extracted = base / "extracted" / "n3 - ninety-nine nights"
            extracted.mkdir(parents=True)
            (extracted / "N3 - Ninety-Nine Nights.iso").write_bytes(b"ISO")
            keys = collect_library_match_keys(base)
            self.assertEqual(
                library_status_for_name("Chrono Trigger (USA).zip", set(), keys),
                "downloaded",
            )
            self.assertEqual(
                library_status_for_name("N3 - Ninety-Nine Nights.zip", set(), keys),
                "downloaded",
            )
            self.assertEqual(
                library_status_for_name(
                    "EarthBound (USA).zip",
                    {"EarthBound (USA).zip"},
                    keys,
                ),
                "queued",
            )
            self.assertEqual(
                library_status_for_name("Missing Game.zip", set(), keys),
                "",
            )
            self.assertEqual(
                library_status_for_name(
                    "Chrono Trigger (USA).zip",
                    {"Chrono Trigger (USA).zip"},
                    keys,
                ),
                "downloaded",
            )

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

    def test_display_filename_and_format_bytes(self):
        self.assertEqual(format_bytes(500), "500 B")
        self.assertEqual(format_bytes(2048), "2.0 KB")
        self.assertTrue(format_bytes(2 * 1024 * 1024).endswith("MB"))
        self.assertEqual(display_filename("folder/game.bin"), "game.bin")
        long_name = "A" * 80 + ".iso"
        shown = display_filename(long_name, max_len=20)
        self.assertEqual(len(shown), 20)
        self.assertIn("...", shown)

    def test_parse_extractor_progress_line(self):
        pct, name = _parse_extractor_progress_line("  45% Track01.bin")
        self.assertEqual(pct, 45)
        self.assertEqual(name, "Track01.bin")
        pct, name = _parse_extractor_progress_line("Extracting  Disc1.iso")
        self.assertIsNone(pct)
        self.assertEqual(name, "Disc1.iso")
        pct, name = _parse_extractor_progress_line("Testing archive game.7z")
        self.assertEqual(name, "game.7z")

    def test_extract_zip_progress_includes_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            zip_path = base / "pack.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("game.nes", "NES ROM DATA")
                zf.writestr("readme.txt", "notes")
            messages = []

            def _cb(pct, status):
                messages.append(status)

            out_dir = base / "out"
            ok = extract_archive(zip_path, out_dir, progress_cb=_cb)
            self.assertTrue(ok)
            joined = "\n".join(messages)
            self.assertIn("game.nes", joined)
            self.assertIn("ZIP", joined)
            self.assertTrue(any("1/2" in m or "2/2" in m for m in messages))

    def test_hidden_subprocess_kwargs_hides_windows_console(self):
        kwargs = _hidden_subprocess_kwargs()
        if sys.platform.startswith("win"):
            self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)
        else:
            self.assertEqual(kwargs, {})

    def test_classify_xbox_iso_offsets(self):
        magic = XDVDFS_MAGIC

        def reader(mapping):
            def read_at(offset, n):
                return mapping.get(offset, b"")[:n]
            return read_at

        self.assertEqual(
            classify_xbox_iso_from_reads(0x10000 + 32, reader({0x10000: magic})),
            "xbox",
        )
        self.assertEqual(
            classify_xbox_iso_from_reads(0x2080000 + 32, reader({0x2080000: magic})),
            "xbox360",
        )
        self.assertEqual(
            classify_xbox_iso_from_reads(0xFD90000 + 32, reader({0xFD90000: magic})),
            "xbox360",
        )
        self.assertEqual(
            classify_xbox_iso_from_reads(0xFDA0000 + 32, reader({0xFDA0000: magic})),
            "xbox360",
        )
        self.assertEqual(
            classify_xbox_iso_from_reads(0x18300000 + 32, reader({0x18300000: magic})),
            "xbox",
        )
        self.assertEqual(
            classify_xbox_iso_from_reads(0x18310000 + 32, reader({0x18310000: magic})),
            "xbox",
        )
        self.assertIsNone(classify_xbox_iso_from_reads(100, reader({})))
        self.assertIsNone(classify_xbox_iso_from_reads(0xFD90000 + 32, reader({0xFD90000: b"not-magic"})))

    def test_classify_xbox_iso_trimmed_xiso_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            iso = pathlib.Path(tmpdir) / "halo.iso"
            with iso.open("wb") as handle:
                handle.write(b"\x00" * 0x10000)
                handle.write(XDVDFS_MAGIC)
            self.assertEqual(classify_xbox_iso(iso), "xbox")
            self.assertEqual(classify_disc_image(iso), "xbox")
            self.assertTrue(should_unpack_xbox_iso(iso))
            self.assertFalse(should_convert_to_chd(iso))

    def test_classify_xbox_iso_redump_xgd2_magic_at_partition_plus_sector32(self):
        """Redump Xbox 360 XGD2 stores XDVDFS at 0xFD90000 + 0x10000."""
        with tempfile.TemporaryDirectory() as tmpdir:
            iso = pathlib.Path(tmpdir) / "N3 - Ninety-Nine Nights.iso"
            offset = 0xFDA0000
            with iso.open("wb") as handle:
                handle.truncate(offset + len(XDVDFS_MAGIC))
                handle.seek(offset)
                handle.write(XDVDFS_MAGIC)
            self.assertEqual(classify_xbox_iso(iso), "xbox360")
            self.assertTrue(should_unpack_xbox_iso(iso))
            folder = pathlib.Path(tmpdir)
            self.assertEqual(collect_xbox_iso_sources(folder), [iso])

    def test_xbox_system_from_hints(self):
        self.assertEqual(
            xbox_system_from_hints("/browse/Redump/Microsoft - Xbox 360/Halo 3.iso"),
            "xbox360",
        )
        self.assertEqual(
            xbox_system_from_hints("Microsoft - Xbox/Halo 2.iso"),
            "xbox",
        )
        self.assertIsNone(xbox_system_from_hints("Microsoft - Xbox One/Forza.iso"))
        self.assertIsNone(xbox_system_from_hints("Sony - PlayStation 2/Game.iso"))

    def test_should_unpack_xbox_iso_from_hints_without_magic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            iso = pathlib.Path(tmpdir) / "Game.iso"
            iso.write_bytes(b"not an xbox header")
            self.assertTrue(
                should_unpack_xbox_iso(
                    iso,
                    context="/browse/Redump/Microsoft - Xbox 360/",
                )
            )
            self.assertFalse(
                should_unpack_xbox_iso(
                    iso,
                    context="/browse/Redump/Sony - PlayStation 2/",
                )
            )

    def test_collect_xbox_iso_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = pathlib.Path(tmpdir) / "extracted" / "Halo 3 (USA)"
            folder.mkdir(parents=True)
            xbox = folder / "Halo 3 (USA).iso"
            with xbox.open("wb") as handle:
                handle.write(b"\x00" * 0x10000)
                handle.write(XDVDFS_MAGIC)
            other = folder / "readme.txt"
            other.write_text("notes")
            found = collect_xbox_iso_sources(folder)
            self.assertEqual(found, [xbox])

    def test_xbox_unpack_command(self):
        iso = pathlib.Path("game.iso")
        out = pathlib.Path("out")
        self.assertEqual(
            xbox_unpack_command({"kind": "xdvdfs", "exe": "xdvdfs"}, iso, out),
            ["xdvdfs", "unpack", "game.iso", "out"],
        )
        self.assertEqual(
            xbox_unpack_command({"kind": "extract-xiso", "exe": "extract-xiso.exe"}, iso, out),
            ["extract-xiso.exe", "-x", "-s", "game.iso", "-d", "out"],
        )

    def test_pick_xdvdfs_release_asset(self):
        assets = [
            {"name": "xdvdfs-web-abc.zip", "browser_download_url": "http://web"},
            {"name": "xdvdfs-windows-abc.zip", "browser_download_url": "http://win"},
            {"name": "xdvdfs-linux-abc.zip", "browser_download_url": "http://linux"},
        ]
        self.assertEqual(pick_xdvdfs_release_asset(assets, windows=True)["browser_download_url"], "http://win")
        self.assertEqual(pick_xdvdfs_release_asset(assets, windows=False)["browser_download_url"], "http://linux")
        self.assertIsNone(pick_xdvdfs_release_asset([], windows=True))

    def test_remove_xbox_system_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = pathlib.Path(tmpdir)
            su = out / "$SystemUpdate"
            su.mkdir()
            (su / "su20076000_00000000").write_bytes(b"update")
            nested = out / "disc" / "$SystemUpdate"
            nested.mkdir(parents=True)
            (nested / "file.bin").write_bytes(b"x")
            (out / "default.xex").write_bytes(b"xex")
            self.assertEqual(remove_xbox_system_update(out), 2)
            self.assertFalse(su.exists())
            self.assertFalse(nested.exists())
            self.assertTrue((out / "default.xex").exists())

    def test_xbox_dump_percent_and_progress_line(self):
        self.assertEqual(xbox_dump_percent(0, 1000), 0)
        self.assertEqual(xbox_dump_percent(500, 1000), 49)
        self.assertEqual(xbox_dump_percent(1000, 1000), 99)
        self.assertEqual(xbox_dump_percent(5000, 1000), 99)
        self.assertEqual(_parse_xbox_unpack_progress_line("Extracting 45%"), 45)
        self.assertEqual(_parse_xbox_unpack_progress_line("100%"), 99)
        self.assertIsNone(_parse_xbox_unpack_progress_line("ok"))

    def test_unpack_xbox_iso_runs_xdvdfs_and_cleans(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            iso = pathlib.Path(tmpdir) / "Halo.iso"
            iso.write_bytes(b"iso")
            out = pathlib.Path(tmpdir) / "out"
            out.mkdir()
            seen = []

            class FakeProc:
                def __init__(self):
                    self.returncode = 0
                    self.stdout = mock.Mock()
                    self.stdout.__iter__ = lambda _self: iter(["ok\n"])
                    self.stdout.close = lambda: None

                def wait(self):
                    nested = out / "Halo"
                    nested.mkdir()
                    (nested / "default.xex").write_bytes(b"xex")
                    su = nested / "$SystemUpdate"
                    su.mkdir()
                    (su / "dash").write_bytes(b"upd")
                    return 0

            def fake_popen(cmd, **kwargs):
                self.assertEqual(cmd[:2], ["xdvdfs", "unpack"])
                return FakeProc()

            with mock.patch("minerva.core.extractors.subprocess.Popen", side_effect=fake_popen):
                ok = unpack_xbox_iso(
                    iso,
                    out,
                    {"kind": "xdvdfs", "exe": "xdvdfs"},
                    progress_cb=lambda pct, status: seen.append((pct, status)),
                    delete_iso=True,
                )
            self.assertTrue(ok)
            self.assertTrue((out / "default.xex").exists())
            self.assertFalse((out / "$SystemUpdate").exists())
            self.assertFalse((out / "Halo" / "$SystemUpdate").exists())
            self.assertFalse(iso.exists())
            self.assertTrue(any(pct == 100 for pct, _ in seen))

    def test_unpack_xbox_isos_in_dir_skips_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = pathlib.Path(tmpdir)
            (folder / "notes.txt").write_text("x")
            self.assertEqual(
                unpack_xbox_isos_in_dir(folder, {"kind": "xdvdfs", "exe": "xdvdfs"}),
                0,
            )


if __name__ == "__main__":
    unittest.main()
