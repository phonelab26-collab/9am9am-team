"""Ligne d'usage Claude : lecture stricte de `claude -p /usage`, jamais de chiffre inventé."""
import unittest

from orch.usage import parse_usage

REAL = """You are currently using your subscription to power your Claude Code usage

Current session: 36% used · resets Oct 2 at 7pm (Africa/Casablanca)
Current week (all models): 31% used · resets Oct 8 at 12am (Africa/Casablanca)

What's contributing to your limits usage?
Approximate, based on local sessions on this machine

Last 24h · 619 requests · 8 sessions
  98% of your usage came from subagent-heavy sessions
"""


class ParseUsageTest(unittest.TestCase):
    def test_real_output_gives_session_and_week(self):
        u = parse_usage(REAL)
        self.assertTrue(u["ok"])
        self.assertEqual([(m["label"], m["pct"], m["reset"], m["level"]) for m in u["meters"]], [
            ("session", 36, "Oct 2 at 7pm (Africa/Casablanca)", "ok"),
            ("semaine", 31, "Oct 8 at 12am (Africa/Casablanca)", "ok")])
        self.assertFalse(u["limit_reached"])

    def test_levels_warn_at_80_and_limit_at_100(self):
        u = parse_usage("Current session: 85% used · resets 7pm\nCurrent week (all models): 100% used · resets Oct 8")
        self.assertEqual([m["level"] for m in u["meters"]], ["warn", "limit"])
        self.assertTrue(u["limit_reached"])
        self.assertEqual(u["limit_reset"], "Oct 8")

    def test_other_weekly_meters_are_kept_with_their_qualifier(self):
        u = parse_usage("Current session: 1% used\nCurrent week (Sonnet only): 4% used · resets Oct 8")
        self.assertEqual([m["label"] for m in u["meters"]], ["session", "semaine (Sonnet only)"])
        self.assertEqual(u["meters"][0]["reset"], "")

    def test_percent_above_100_is_capped_and_garbage_percent_ignored(self):
        u = parse_usage("Current session: 140% used · resets 7pm")
        self.assertEqual(u["meters"][0]["pct"], 100)

    def test_unreadable_output_is_unknown_never_invented(self):
        for raw in ("", "Not logged in", "Usage: claude [options]", None, "Current session: beaucoup used"):
            with self.subTest(raw=raw):
                u = parse_usage(raw)
                self.assertFalse(u["ok"])
                self.assertNotIn("meters", u)
                self.assertTrue(u["error"])


if __name__ == "__main__":
    unittest.main()
