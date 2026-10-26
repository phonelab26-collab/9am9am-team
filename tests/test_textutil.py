import unittest

from orch.textutil import JOURNAL_WRAP, journal_lines, norm_goal, tail


class JournalLinesTest(unittest.TestCase):
    def test_one_item_per_text_line(self):
        self.assertEqual(journal_lines("a\nb"), ["a", "b"])

    def test_blank_line_kept(self):
        self.assertEqual(journal_lines("a\n\nb"), ["a", "", "b"])

    def test_empty_and_none_give_one_blank_line(self):
        self.assertEqual(journal_lines(""), [""])
        self.assertEqual(journal_lines(None), [""])

    def test_long_line_wrapped_without_loss(self):
        lines = journal_lines("mot " * 60)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(x) <= JOURNAL_WRAP for x in lines))
        self.assertEqual(" ".join(lines).split(), ["mot"] * 60)

    def test_giant_word_is_cut(self):
        self.assertTrue(all(len(x) <= JOURNAL_WRAP for x in journal_lines("x" * 250)))

    def test_tabs_crlf_and_indentation(self):
        self.assertNotIn("\t", "".join(journal_lines("a\tb")))
        self.assertEqual(journal_lines("a\r\nb"), ["a", "b"])
        self.assertEqual(journal_lines("  - point"), ["  - point"])


class TailAndGoalTest(unittest.TestCase):
    def test_tail_keeps_the_end_and_says_so(self):
        out = tail("a" * 3000 + "FIN", 100)
        self.assertTrue(out.endswith("FIN"))
        self.assertIn("début omis", out)
        self.assertEqual(tail("court", 100), "court")
        self.assertEqual(tail(None, 10), "")

    def test_norm_goal_strips_and_bounds(self):
        self.assertEqual(norm_goal("  G  "), "G")
        self.assertEqual(norm_goal(None), "")
        self.assertEqual(len(norm_goal("x" * 5000)), 2000)


if __name__ == "__main__":
    unittest.main()
