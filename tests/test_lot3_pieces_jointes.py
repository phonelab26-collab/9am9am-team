"""Lot 3, point 2 : le texte et les pièces jointes ont chacun leur plafond ; rien n'est coupé en douce."""
import json
import unittest

from orch.workspace import ATTACH_MAX, attached_names, fit_block
from tests.test_engine import EngineCase, FakeCeo, FakeWorkers


def block(*chunks):
    return "Fichiers joints (donnees : lis-les pour travailler dessus) :\n" + "\n\n".join(chunks)


def chunk(name, size, suffix=""):
    return f"<<<FICHIER {name}{suffix}\n{'x' * size}\nFICHIER>>>"


class FitBlockTest(unittest.TestCase):
    def test_a_block_that_fits_is_kept_whole(self):
        b = block(chunk("a.md", 100), chunk("b.md", 100))
        self.assertEqual(fit_block(b, ATTACH_MAX), (b, 0))

    def test_whole_files_are_dropped_never_a_fence_cut(self):
        b = block(chunk("a.md", 3000), chunk("b.md", 3000), chunk("c.md", 3000))
        kept, dropped = fit_block(b, 7000)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept.count("<<<FICHIER"), kept.count("FICHIER>>>"))
        self.assertIn("a.md", kept)
        self.assertNotIn("c.md", kept)
        self.assertLessEqual(len(kept), 7000)

    def test_names_come_back_for_the_user_bubble(self):
        b = block(chunk("a.md", 5), chunk("notes.txt", 5, " (de ton Mac)"))
        self.assertEqual(attached_names(b), ["a.md", "notes.txt"])


class EngineAttachmentsTest(EngineCase):
    def converse(self, message, attachments="", ceo=None):
        self.ceo = ceo or FakeCeo()
        self.ceo.chat_default = json.dumps({"reply": "Lu.", "tasks": []})
        return self.engine().converse(message, attachments=attachments)

    def test_long_text_and_attachments_both_reach_the_ceo_intact(self):
        text = "Q" * 3900
        att = block(chunk("a.md", 6000), chunk("b.md", 6000), chunk("c.md", 6000))
        self.converse(text, att)
        prompt = self.ceo.prompts[0]  # le prompt neutralise « <<< » en « ‹‹‹ » : données, pas des balises
        for name in ("a.md", "b.md", "c.md"):
            self.assertTrue(f"‹‹‹FICHIER {name}" in prompt, name)
        self.assertEqual(prompt.count("‹‹‹FICHIER"), prompt.count("FICHIER›››"))
        self.assertTrue(text in prompt)

    def test_the_users_bubble_shows_no_raw_file_block(self):
        self.converse("Résume", block(chunk("a.md", 50)))
        user = self.chat.since(0)[0]
        self.assertEqual((user["role"], user["text"]), ("user", "Résume"))
        self.assertNotIn("FICHIER", user["text"])
        self.assertEqual(user["data"]["files"], ["a.md"])

    def test_an_oversized_attachment_set_is_trimmed_by_whole_files_and_said(self):
        att = block(*[chunk(f"f{i}.md", 6000) for i in range(6)])  # ≈ 36 k > plafond
        self.converse("Résume", att)
        prompt = self.ceo.prompts[0]
        self.assertEqual(prompt.count("‹‹‹FICHIER"), prompt.count("FICHIER›››"))
        self.assertLess(prompt.count("‹‹‹FICHIER"), 6)
        said = [m for m in self.chat.since(0) if m.get("kind") == "attachments_dropped"]
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0]["text"], r"\d fichier")

    def test_a_text_longer_than_the_cap_is_cut_and_said(self):
        self.converse("Z" * 4500)
        said = [m for m in self.chat.since(0) if m.get("kind") == "message_cut"]
        self.assertEqual(len(said), 1)
        self.assertIn("4000", said[0]["text"])


if __name__ == "__main__":
    unittest.main()
