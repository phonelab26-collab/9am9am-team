"""Onglet workspace : dossier de travail + fichiers joints au prompt."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch import workspace as W


class CleanRelTest(unittest.TestCase):
    def test_rejects_escape_and_absolute(self):
        for bad in ("", None, 3, "/etc/passwd", "~/x", "../a", "a/../../b",
                    "x" * 600):
            self.assertEqual(W.clean_rel(bad), "")
        self.assertEqual(W.clean_rel("  a/b.txt  "), "a/b.txt")
        self.assertEqual(W.clean_rel("./a"), "a")


class ListDirTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-ws-"))
        (self.dir / "sub").mkdir()
        (self.dir / "a.md").write_text("hello", encoding="utf-8")
        (self.dir / ".mini-orch").mkdir()
        (self.dir / ".mini-orch" / "x").write_text("x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_lists_and_hides_state(self):
        entries, note = W.list_dir(self.dir, "")
        names = [e["name"] for e in entries]
        self.assertIn("a.md", names)
        self.assertIn("sub/", names)
        self.assertFalse(any(".mini-orch" in n for n in names))
        self.assertEqual(note, "")

    def test_outside_is_refused(self):
        entries, err = W.list_dir(self.dir, "../x")
        self.assertEqual(entries, [])
        self.assertTrue(err)

    def test_missing_folder_reported(self):
        self.assertEqual(W.list_dir(self.dir, "nope"), ([], "dossier introuvable."))

    def test_file_instead_of_folder_reported(self):
        self.assertEqual(W.list_dir(self.dir, "a.md"), ([], "ce n'est pas un dossier."))

    def test_uncleanable_path_reported(self):
        # « // » ou « . » se nettoient en vide : le raw était pourtant renseigné.
        for raw in ("//", ".", "   "):
            self.assertEqual(W.list_dir(self.dir, raw),
                             ([], "chemin hors du dossier de travail."))

    def test_symlink_outside_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="orch-ws-out-"))
        (outside / "loin.md").write_text("x", encoding="utf-8")
        (self.dir / "lien").symlink_to(outside)
        entries, err = W.list_dir(self.dir, "lien")
        self.assertEqual(entries, [])
        self.assertEqual(err, "chemin hors du dossier de travail.")

    def test_sub_listing_shows_parent_and_hides_dotfiles(self):
        sub = self.dir / "sub"
        (sub / ".secret").write_text("x", encoding="utf-8")   # dot : caché
        (sub / ".env").write_text("x", encoding="utf-8")      # dot autorisé
        (sub / ".git").mkdir()                                # SKIP_DIRS
        entries, err = W.list_dir(self.dir, "sub")
        self.assertEqual(err, "")
        names = [e["name"] for e in entries]
        self.assertEqual(names[0], ".. (parent)")
        self.assertIn(".env", names)
        self.assertNotIn(".secret", names)
        self.assertNotIn(".git/", names)

    def test_broken_symlink_is_skipped(self):
        (self.dir / "sub" / "mort").symlink_to(self.dir / "jamais")
        entries, err = W.list_dir(self.dir, "sub")
        self.assertEqual(err, "")
        self.assertFalse(any(e["name"] == "mort" for e in entries))

    def test_listing_is_bounded(self):
        for i in range(W.MAX_LIST + 25):
            (self.dir / "sub" / f"f{i:03}").touch()
        entries, err = W.list_dir(self.dir, "sub")
        self.assertIn("tronquee", err)  # 25 entrées restent hors liste
        files = [e for e in entries if e["name"] != ".. (parent)"]
        self.assertEqual(len(files), W.MAX_LIST)

    def test_iterdir_oserror_reported(self):
        with mock.patch.object(Path, "iterdir", side_effect=OSError("disque mort")):
            entries, err = W.list_dir(self.dir, "")
        self.assertEqual(entries, [])
        self.assertIn("lecture impossible", err)


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-ws-"))
        (self.dir / "ok.md").write_text("contenu", encoding="utf-8")
        (self.dir / "bin.png").write_bytes(b"\x89PNG\x00\x01")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_read_ok_and_binary_refused(self):
        text, err = W.read_for_prompt(self.dir, "ok.md")
        self.assertEqual((text, err), ("contenu", ""))
        _, err2 = W.read_for_prompt(self.dir, "bin.png")
        self.assertIn("texte", err2)

    def test_folder_instead_of_file_reported(self):
        (self.dir / "doss").mkdir()
        _, err = W.read_for_prompt(self.dir, "doss")
        self.assertIn("fichier introuvable", err)

    def test_symlink_outside_reported(self):
        outside = Path(tempfile.mkdtemp(prefix="orch-ws-out-")) / "loin.md"
        outside.write_text("secret", encoding="utf-8")
        (self.dir / "lien.md").symlink_to(outside)
        _, err = W.read_for_prompt(self.dir, "lien.md")
        self.assertIn("hors du dossier de travail", err)

    def test_oversized_file_refused(self):
        (self.dir / "gros.md").write_bytes(b"x" * 300_001)
        _, err = W.read_for_prompt(self.dir, "gros.md")
        self.assertIn("trop gros", err)

    def test_long_text_is_truncated_from_the_top(self):
        (self.dir / "long.md").write_text("tete\n" + "y" * (W.MAX_FILE_CHARS + 100),
                                          encoding="utf-8")
        text, err = W.read_for_prompt(self.dir, "long.md")
        self.assertEqual(err, "")
        self.assertTrue(text.startswith("(debut omis)"))
        self.assertLessEqual(len(text), W.MAX_FILE_CHARS + len("(debut omis) "))
        self.assertNotIn("tete", text)  # c'est le DÉBUT qui saute

    def test_block_is_bounded(self):
        (self.dir / "big.md").write_text("y" * 20000, encoding="utf-8")
        block, notes = W.build_block(self.dir, ["ok.md", "nope.md", "bin.png"])
        self.assertIn("ok.md", block)
        self.assertIn("FICHIER>>>", block)
        self.assertEqual(block.count("FICHIER>>>"), 1)
        self.assertTrue(any("introuvable" in n or "binaire" in n or "texte" in n
                            for n in notes))
        many = ["ok.md"] + ["f%d.md" % i for i in range(9)]
        for i in range(9):
            (self.dir / ("f%d.md" % i)).write_text("z", encoding="utf-8")
        _, notes2 = W.build_block(self.dir, many)
        self.assertTrue(any("premiers" in n for n in notes2))

    def test_pasted_local_files_are_framed_and_bounded(self):
        block, notes = W.build_block(
            self.dir, [], [{"name": "../evil.txt", "text": "salut"},
                           {"name": "note.txt", "text": "x" * 20000},
                           {"name": "", "text": "vide"}])
        self.assertIn("evil.txt (de ton Mac)", block)
        self.assertIn("note.txt", block)
        self.assertEqual(block.count("FICHIER>>>"), 2)
        self.assertIn("(debut omis)", block)
        self.assertEqual(notes, [])
        bad, notes2 = W.build_block(self.dir, [], [{"name": "b.bin", "text": "a\x00b"}])
        self.assertEqual(bad, "")
        self.assertTrue(any("binaire" in n for n in notes2))

    def test_total_attachment_limit_reached(self):
        for i in range(3):
            (self.dir / f"p{i}.md").write_text("z" * 6000, encoding="utf-8")
        block, notes = W.build_block(self.dir, ["p0.md", "p1.md", "p2.md"])
        self.assertEqual(block.count("FICHIER>>>"), 1)  # seul le premier tient
        self.assertEqual(len([n for n in notes if "limite totale" in n]), 2)

    def test_pasted_files_beyond_the_cap_are_dropped(self):
        entries = [{"name": f"n{i}.txt", "text": "x"} for i in range(W.MAX_FILES + 1)]
        block, notes = W.build_block(self.dir, [], entries)
        self.assertEqual(block.count("FICHIER>>>"), W.MAX_FILES)
        self.assertTrue(any("en trop" in n for n in notes))

    def test_pasted_total_limit(self):
        entries = [{"name": f"n{i}.txt", "text": "x" * 6000} for i in range(W.MAX_FILES)]
        block, notes = W.build_block(self.dir, [], entries)
        self.assertTrue(any("de ton Mac : limite totale" in n for n in notes))
        self.assertLess(block.count("FICHIER>>>"), W.MAX_FILES)


if __name__ == "__main__":
    unittest.main()
