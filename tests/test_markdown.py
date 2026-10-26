"""Mise en forme des réponses (titres, listes, code, gras) : module pur orch/web/markdown.js,
exécuté par Node. Il ne produit que des données ; la page les affiche avec textContent (jamais
de HTML injecté)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "orch" / "web" / "markdown.js"
NODE = shutil.which("node")


def run(fn, arg):
    """Appelle Markdown.<fn>(arg) dans Node et renvoie le JSON obtenu."""
    script = ("const m = require(process.argv[1]); "
              "console.log(JSON.stringify(m[process.argv[2]](JSON.parse(process.argv[3]))));")
    out = subprocess.run([NODE, "-e", script, str(MODULE), fn, json.dumps(arg)],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def T(v):
    return {"t": "text", "v": v}


BR = {"t": "br"}


@unittest.skipUnless(NODE, "Node.js absent : rendu non testable ici")
class BlocksTest(unittest.TestCase):
    def blocks(self, src):
        return run("parseMarkdown", src)

    def test_every_line_is_its_own_paragraph_so_plain_text_is_not_one_block(self):
        self.assertEqual(self.blocks("Bonjour\nmonde\n\nSuite"), [
            {"type": "para", "inline": [T("Bonjour")]},
            {"type": "para", "inline": [T("monde")]},
            {"type": "para", "inline": [T("Suite")]}])

    def test_a_plain_multi_line_report_gives_one_paragraph_per_line(self):
        report = ("J'ai demandé à mon équipe un audit.\nSécurité : une faille haute.\n"
                  "Tests : à faire.\nJe lance ?")
        got = self.blocks(report)
        self.assertEqual([b["type"] for b in got], ["para"] * 4)
        self.assertEqual(got[1]["inline"], [T("Sécurité : une faille haute.")])

    def test_a_structured_report_keeps_headings_and_bullets(self):
        report = "Tout est fait.\n\n## Fait\n- A\n- B\n\n## Échoué ou à faire\n- C\n\n**Suite proposée :** relancer."
        got = self.blocks(report)
        self.assertEqual([b["type"] for b in got], ["para", "heading", "list", "heading", "list", "para"])
        self.assertEqual(got[5]["inline"][0], {"t": "strong", "v": "Suite proposée :"})

    def test_headings_have_a_level_and_inline_formatting(self):
        self.assertEqual(self.blocks("## Titre **gras**\n###### Petit"), [
            {"type": "heading", "level": 2, "inline": [T("Titre "), {"t": "strong", "v": "gras"}]},
            {"type": "heading", "level": 6, "inline": [T("Petit")]}])

    def test_bullet_and_numbered_lists_are_grouped_with_depth(self):
        self.assertEqual(self.blocks("- a\n* b\n  - sous-b\n\n1. un\n2. deux"), [
            {"type": "list", "ordered": False, "items": [
                {"depth": 0, "inline": [T("a")]}, {"depth": 0, "inline": [T("b")]},
                {"depth": 1, "inline": [T("sous-b")]}]},
            {"type": "list", "ordered": True, "items": [
                {"depth": 0, "inline": [T("un")]}, {"depth": 0, "inline": [T("deux")]}]}])

    def test_code_fence_keeps_language_and_text_verbatim(self):
        self.assertEqual(self.blocks("avant\n```py\nprint(1)\n\n# **pas gras**\n```\naprès"), [
            {"type": "para", "inline": [T("avant")]},
            {"type": "code", "lang": "py", "text": "print(1)\n\n# **pas gras**"},
            {"type": "para", "inline": [T("après")]}])

    def test_an_unclosed_fence_is_still_shown_as_code(self):
        self.assertEqual(self.blocks("```\nencore en cours"),
                         [{"type": "code", "lang": "", "text": "encore en cours"}])

    def test_tables_stay_aligned_as_monospace_text(self):
        got = self.blocks("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertEqual(got, [{"type": "code", "lang": "table", "text": "| a | b |\n|---|---|\n| 1 | 2 |"}])

    def test_quote_and_rule(self):
        self.assertEqual(self.blocks("> cité\n\n---"), [
            {"type": "quote", "inline": [T("cité")]}, {"type": "hr"}])

    def test_inline_triple_backticks_are_not_a_block_and_lose_no_text(self):
        self.assertEqual(self.blocks("```ls``` puis du texte\n\n# Titre\nfin"), [
            {"type": "para", "inline": [{"t": "code", "v": "ls"}, T(" puis du texte")]},
            {"type": "heading", "level": 1, "inline": [T("Titre")]},
            {"type": "para", "inline": [T("fin")]}])

    def test_a_cut_start_is_flagged_and_a_cut_code_block_is_still_code(self):
        # tail() garde la fin d'une réponse trop longue : « …(début omis) » + la fin, parfois en plein bloc de code.
        self.assertEqual(self.blocks("…(début omis) tie du code\nsuite\n```\napres"), [
            {"type": "note", "text": "début omis"},
            {"type": "code", "lang": "", "text": "tie du code\nsuite"},
            {"type": "para", "inline": [T("apres")]}])
        self.assertEqual(self.blocks("…(début omis) ligne\n```\ncode\n```\nfin"), [
            {"type": "note", "text": "début omis"},
            {"type": "para", "inline": [T("ligne")]},
            {"type": "code", "lang": "", "text": "code"},
            {"type": "para", "inline": [T("fin")]}])

    def test_empty_or_non_text_input_gives_no_blocks(self):
        for junk in ("", "   \n\n", None, 12, ["x"], {}):
            with self.subTest(junk=junk):
                self.assertEqual(self.blocks(junk), [])

    def test_html_is_plain_text_never_markup(self):
        self.assertEqual(self.blocks("<img src=x onerror=alert(1)>"),
                         [{"type": "para", "inline": [T("<img src=x onerror=alert(1)>")]}])


@unittest.skipUnless(NODE, "Node.js absent : rendu non testable ici")
class InlineTest(unittest.TestCase):
    def inline(self, src):
        return run("parseInline", src)

    def test_code_bold_and_italic(self):
        self.assertEqual(self.inline("`x` puis **g** et *i*"), [
            {"t": "code", "v": "x"}, T(" puis "), {"t": "strong", "v": "g"}, T(" et "), {"t": "em", "v": "i"}])

    def test_formatting_marks_inside_code_are_literal(self):
        self.assertEqual(self.inline("`**a**`"), [{"t": "code", "v": "**a**"}])

    def test_look_alikes_are_left_alone(self):
        for plain in ("snake_case_name", "2 * 3 * 4", "**non fermé", "`non fermé", "a ** b ** c", "5*"):
            with self.subTest(plain=plain):
                self.assertEqual(self.inline(plain), [T(plain)])

    def test_a_run_of_backticks_closes_with_a_run_of_the_same_length(self):
        self.assertEqual(self.inline("``a`b`` et ```ls```"), [
            {"t": "code", "v": "a`b"}, T(" et "), {"t": "code", "v": "ls"}])

    def test_globs_products_and_powers_are_not_emphasis(self):
        for plain in ("src/*.py et tests/*.py; 2*3*4", "2**3**4", "a*b*c", "x**y**z"):
            with self.subTest(plain=plain):
                self.assertEqual(self.inline(plain), [T(plain)])

    def test_emphasis_is_accepted_next_to_punctuation(self):
        self.assertEqual(self.inline("(*i*) et *j*."), [
            T("("), {"t": "em", "v": "i"}, T(") et "), {"t": "em", "v": "j"}, T(".")])
        self.assertEqual(self.inline("**g**: ok"), [{"t": "strong", "v": "g"}, T(": ok")])

    def test_adversarial_input_stays_fast(self):
        script = ("const m = require(process.argv[1]); const t = Date.now();"
                  "m.parseInline('*a '.repeat(30000)); m.parseInline('**a '.repeat(30000)); m.parseInline('`a '.repeat(30000));"
                  "m.parseMarkdown('\\n'.repeat(30000) + '*a '.repeat(10000));"
                  "console.log(Date.now() - t);")
        out = subprocess.run([NODE, "-e", script, str(MODULE)], capture_output=True, text=True, timeout=60, check=True)
        self.assertLess(int(out.stdout), 3000, "analyse trop lente sur un texte hostile")

    def test_adjacent_text_is_merged(self):
        self.assertEqual(self.inline("a b c"), [T("a b c")])


if __name__ == "__main__":
    unittest.main()
