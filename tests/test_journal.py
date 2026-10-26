"""Journal : horodatage, rotation, et dégradation propre quand le disque refuse."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.config import Paths
from orch.journal import MAX_BYTES, Journal
from orch.lockfile import LockTimeout


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-jrn-"))
        self.paths = Paths.for_root(self.dir)
        self.paths.journal.parent.mkdir(parents=True, exist_ok=True)
        self.echoed = []

    def test_writes_timestamped_line_and_echoes(self):
        Journal(self.paths, self.echoed.append)("bonjour\nà la ligne")
        raw = self.paths.journal.read_text(encoding="utf-8")
        self.assertEqual(self.echoed, ["bonjour\nà la ligne"])
        self.assertRegex(raw, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} bonjour\n    à la ligne\n$")

    def test_appends_from_several_instances(self):
        Journal(self.paths, None)("premier")
        Journal(self.paths, None)("second")
        lines = self.paths.journal.read_text(encoding="utf-8").splitlines()
        self.assertEqual([ln.split(" ", 2)[2] for ln in lines], ["premier", "second"])

    def test_oversized_log_rotated_at_startup(self):
        self.paths.journal.write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
        Journal(self.paths, None)("après rotation")
        self.assertEqual(self.paths.journal.with_name("journal.log.1").read_text(encoding="utf-8"),
                         "x" * (MAX_BYTES + 1))
        self.assertIn("après rotation", self.paths.journal.read_text(encoding="utf-8"))

    def test_small_log_not_rotated(self):
        self.paths.journal.write_text("petit", encoding="utf-8")
        Journal(self.paths, None)("suite")
        self.assertFalse(self.paths.journal.with_name("journal.log.1").exists())
        raw = self.paths.journal.read_text(encoding="utf-8")
        self.assertTrue(raw.startswith("petit"))  # pas de troncature
        self.assertIn(" suite", raw)

    def test_unwritable_journal_warns_exactly_once(self):
        # Disque plein / permissions : le message part une seule fois, puis le
        # journal reste muet sur disque — mais l'écho console continue.
        j = Journal(self.paths, self.echoed.append)
        with mock.patch("os.open", side_effect=PermissionError("nope")):
            j("msg 1")
            j("msg 2")
        warns = [m for m in self.echoed if "inaccessible" in m]
        self.assertEqual(len(warns), 1)
        self.assertIn("PermissionError", warns[0])
        self.assertEqual(self.echoed[-1], "msg 2")  # l'écho normal passe quand même

    def test_rotation_failure_warns(self):
        with mock.patch("orch.journal.lock_for") as lock:
            lock.return_value.held.side_effect = OSError("lock cassé")
            j = Journal(self.paths, self.echoed.append)
        self.assertEqual(len([m for m in self.echoed if "inaccessible" in m]), 1)
        # Le journal reste utilisable ensuite.
        j("après")
        self.assertIn("après", self.paths.journal.read_text(encoding="utf-8"))

    def test_rotation_lock_timeout_is_not_fatal(self):
        # Un autre process tient le verrou d'état : la rotation est perdue, pas le
        # démarrage (un LockTimeout ne doit jamais tuer l'application au lancement).
        with mock.patch("orch.journal.lock_for") as lock:
            lock.return_value.held.side_effect = LockTimeout("état occupé")
            j = Journal(self.paths, self.echoed.append)
        self.assertEqual(len([m for m in self.echoed if "inaccessible" in m]), 1)
        j("après")
        self.assertIn("après", self.paths.journal.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
