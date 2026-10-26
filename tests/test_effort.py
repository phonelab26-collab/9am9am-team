"""Réglage d'effort par agent : faible / normal / élevé / max / ultracode (ou défaut = vide)."""
import unittest

from orch.effort import CLI_EFFORT, LEVELS, clean_effort


class CleanEffortTest(unittest.TestCase):
    def test_the_five_levels_map_to_the_claude_cli_vocabulary(self):
        self.assertEqual(LEVELS, ("faible", "normal", "élevé", "max", "ultracode"))
        self.assertEqual([CLI_EFFORT[l] for l in LEVELS], ["low", "medium", "high", "max", "xhigh"])

    def test_accepts_levels_with_case_spaces_and_missing_accent(self):
        for raw, want in (("faible", "faible"), (" Normal ", "normal"), ("ÉLEVÉ", "élevé"),
                          ("eleve", "élevé"), ("MAX", "max"), ("Ultracode", "ultracode")):
            with self.subTest(raw=raw):
                self.assertEqual(clean_effort(raw), want)

    def test_empty_means_default_and_junk_is_refused_not_guessed(self):
        self.assertEqual(clean_effort(""), "")
        self.assertEqual(clean_effort("   "), "")
        for bad in ("turbo", "high", "--effort", "max; rm -rf", None, 5, "élevé max"):
            with self.subTest(bad=bad):
                self.assertIsNone(clean_effort(bad))


if __name__ == "__main__":
    unittest.main()
