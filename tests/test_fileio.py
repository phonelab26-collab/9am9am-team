"""Écritures atomiques : le fichier final n'existe jamais à moitié écrit,
et un échec ne laisse jamais de détritus ni ne détruit l'ancienne version."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.fileio import MISSING, atomic_write_json, cleanup_tmp, quarantine, read_json


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-fio-"))
        self.path = self.dir / "data.json"

    def test_roundtrip_creates_private_file(self):
        atomic_write_json(self.path, {"a": 1})
        self.assertEqual(read_json(self.path), {"a": 1})
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{\n  "a": 1\n}\n')
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)  # umask neutralisé

    def test_overwrite_keeps_no_tmp_behind(self):
        atomic_write_json(self.path, {"v": 1})
        atomic_write_json(self.path, {"v": 2})
        self.assertEqual(read_json(self.path), {"v": 2})
        self.assertEqual(list(self.dir.glob(".*.tmp")), [], "un .tmp a survécu au remplacement")

    def test_failure_cleans_tmp_and_preserves_previous_content(self):
        atomic_write_json(self.path, {"safe": True})
        with self.assertRaises(TypeError):  # json.dump ne sait pas écrire un set
            atomic_write_json(self.path, {"bad": {1}})
        self.assertEqual(read_json(self.path), {"safe": True},
                         "l'ancien contenu doit survivre à l'échec")
        self.assertEqual(list(self.dir.glob(".*.tmp")), [],
                         "le fichier temporaire n'a pas été nettoyé")

    def test_chmod_parent_failure_is_not_fatal(self):
        # Un dossier que l'on ne peut pas chmod ne doit pas empêcher l'écriture
        # (le chmod du tmp, lui, doit rester efficace).
        # Le dossier est chmodé par son descripteur (vérifié sans lien) : fchmod.
        real_fchmod, calls = os.fchmod, []

        def flaky(fd, mode):
            calls.append(mode)
            if mode == 0o700:  # le dossier
                raise OSError("read-only")
            return real_fchmod(fd, mode)

        with mock.patch("os.fchmod", side_effect=flaky):
            atomic_write_json(self.path, {"ok": 1})
        self.assertEqual(read_json(self.path), {"ok": 1})
        self.assertEqual(calls, [0o700, 0o600])  # parent (échec ignoré) + tmp (réussi)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_fsync_falls_back_without_fullfsync(self):
        # Sans F_FULLFSYNC (Linux, ou fd non supporté) : fsync classique.
        with mock.patch("fcntl.fcntl", side_effect=OSError("no fullfsync")) as fcntl_call:
            with mock.patch("os.fsync") as fsync:
                atomic_write_json(self.path, {"a": 1})
        self.assertTrue(fcntl_call.called)
        self.assertEqual(fsync.call_count, 2)  # fichier + répertoire parent

    def test_read_missing_sentinel(self):
        self.assertIs(read_json(self.dir / "nope.json"), MISSING)

    def test_read_invalid_json_raises_value_error(self):
        self.path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_json(self.path)


class QuarantineTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-fio-"))
        self.path = self.dir / "tasks.json"

    def test_quarantines_without_overwriting_previous_one(self):
        self.path.write_text("{broken 1", encoding="utf-8")
        first = quarantine(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(first.read_text(encoding="utf-8"), "{broken 1")
        # Une seconde panne dans la même seconde : la 1ʳᵉ quarantaine est préservée.
        self.path.write_text("{broken 2", encoding="utf-8")
        second = quarantine(self.path)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "{broken 1")
        self.assertEqual(second.read_text(encoding="utf-8"), "{broken 2")


class CleanupTmpTest(unittest.TestCase):
    def test_removes_crash_leftovers_only(self):
        d = Path(tempfile.mkdtemp(prefix="orch-fio-"))
        (d / ".tasks.json.abc123.tmp").write_text("{}", encoding="utf-8")
        (d / ".config.json.x.tmp").write_text("{}", encoding="utf-8")
        (d / "tasks.json").write_text("[]", encoding="utf-8")  # pas un .tmp : intouché
        cleanup_tmp(d)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["tasks.json"])


if __name__ == "__main__":
    unittest.main()
