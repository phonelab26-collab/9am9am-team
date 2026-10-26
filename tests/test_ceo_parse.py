import json
import unittest

from orch.ceo import (LEAN_ARGS, READ_TOOLS, Ceo, PlanError, clean_cloud_session, clean_ceo_mode, clean_model, effort_advice,
                      parse_chat, parse_plan, parse_verdict)
from orch.procs import CmdResult


class ParseVerdictTest(unittest.TestCase):
    CASES = [
        ("VERDICT: ok\nSuite : passer à la 3", ("ok", "Suite : passer à la 3")),
        ("verdict: KO — fichier vide", ("ko", "fichier vide")),
        ("**VERDICT: ko**", ("ko", "")),
        ("**VERDICT:** ko\nvide", ("ko", "vide")),
    ]
    VERDICT_ONLY = [
        ("Je ne peux pas dire VERDICT: ok car vide.\nVERDICT: ko", "ko"),
        ("**VERDICT**: ko", "ko"),
        ("VERDICT : ok", "ok"),
        ("1. VERDICT: ok", "ok"),
        ("VERDICT: ok (détail)", "ok"),
        ("Verdict : `ko`", "ko"),
    ]
    NONE = [
        "blabla",
        "CEO-ERROR(code=1): x",
        "CEO-ERROR(code=1): x\nVERDICT: ok",
        "Format : VERDICT: ok|ko. Je ne peux pas juger.",
        "VERDICT: ko\nVERDICT: ok",
        "VERDICT: ko ou ok",
        "VERDICT: ok / ko",
        "VERDICT: okay",
    ]

    def test_full_cases(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(parse_verdict(text), expected)

    def test_verdict_only_cases(self):
        for text, expected in self.VERDICT_ONLY:
            with self.subTest(text=text):
                self.assertEqual(parse_verdict(text)[0], expected)

    def test_no_verdict_cases(self):
        for text in self.NONE:
            with self.subTest(text=text):
                self.assertIsNone(parse_verdict(text)[0])


class ParsePlanTest(unittest.TestCase):
    def test_plain_json(self):
        items, notes = parse_plan('[{"title": "A", "worker": "opencode", "detail": "d"}]', ["opencode"])
        self.assertEqual(items, [{"title": "A", "worker": "opencode", "detail": "d"}])
        self.assertEqual(notes, [])

    def test_noise_and_brackets_around_json(self):
        raw = 'Voici [le plan] :\n```json\n[{"title": "A", "worker": "opencode"}]\n```\nfin [x]'
        self.assertEqual(parse_plan(raw, ["opencode"])[0][0]["title"], "A")

    def test_unavailable_worker_replaced_and_reported(self):
        items, notes = parse_plan('[{"title": "A", "worker": "cline"}]', ["opencode"])
        self.assertEqual(items[0]["worker"], "opencode")
        self.assertTrue(any("cline" in n for n in notes))

    def test_invalid_items_reported(self):
        items, notes = parse_plan('[{"title": ""}, 3, {"title": "B", "worker": "opencode"}]', ["opencode"])
        self.assertEqual([i["title"] for i in items], ["B"])
        self.assertEqual(len(notes), 2)

    def test_errors_are_explicit(self):
        for raw in ("pas de json", "[]", '[{"title": ""}]', "[" + ",".join(['{"title": "x"}'] * 7) + "]"):
            with self.subTest(raw=raw[:30]), self.assertRaises(PlanError):
                parse_plan(raw, ["opencode"])

    def test_fields_are_bounded(self):
        items, _ = parse_plan('[{"title": "%s", "detail": "%s"}]' % ("t" * 500, "d" * 5000), ["opencode"])
        self.assertEqual((len(items[0]["title"]), len(items[0]["detail"])), (200, 2000))


class ParseChatTest(unittest.TestCase):
    W = ["opencode"]

    def test_direct_answer(self):
        reply, items, notes = parse_chat('{"reply": "Oui, c\'est moi.", "tasks": []}', self.W)
        self.assertEqual((reply, items, notes), ("Oui, c'est moi.", [], []))

    def test_delegation(self):
        raw = '{"reply": "Je m\'en occupe.", "tasks": [{"title": "A", "worker": "opencode", "detail": "d"}]}'
        reply, items, _ = parse_chat(raw, self.W)
        self.assertEqual(reply, "Je m'en occupe.")
        self.assertEqual(items, [{"title": "A", "worker": "opencode", "detail": "d"}])

    def test_json_inside_noise_or_fences(self):
        raw = 'Voici :\n```json\n{"reply": "ok", "tasks": []}\n```\nfin {x}'
        self.assertEqual(parse_chat(raw, self.W)[0], "ok")

    def test_prose_is_a_plain_answer(self):
        reply, items, notes = parse_chat("Bonjour, ça va bien.", self.W)
        self.assertEqual((reply, items), ("Bonjour, ça va bien.", []))
        self.assertTrue(notes)

    def test_unavailable_worker_replaced_and_invalid_items_dropped(self):
        raw = '{"reply": "r", "tasks": [{"title": "A", "worker": "cline"}, 3, {"title": ""}]}'
        _, items, notes = parse_chat(raw, self.W)
        self.assertEqual([(i["title"], i["worker"]) for i in items], [("A", "opencode")])
        self.assertEqual(len(notes), 3)

    def test_too_many_tasks_are_truncated_and_reported(self):
        raw = '{"reply": "r", "tasks": [' + ",".join(['{"title": "x", "worker": "opencode"}'] * 9) + "]}"
        _, items, notes = parse_chat(raw, self.W)
        self.assertEqual(len(items), 6)
        self.assertTrue(any("6" in n for n in notes))

    def test_tasks_not_a_list_and_reply_bounded(self):
        reply, items, notes = parse_chat('{"reply": "%s", "tasks": "faire"}' % ("x" * 9000), self.W)
        self.assertEqual((len(reply) <= 4000, items), (True, []))
        self.assertTrue(notes)


class CleanModelTest(unittest.TestCase):
    def test_accepts_aliases_and_full_names(self):
        for ok in ("sonnet", "opus", "haiku", "claude-sonnet-5", "claude-haiku-4-5-20251001", "opus[1m]",
                   "anthropic/claude-x", " sonnet "):
            with self.subTest(ok=ok):
                self.assertEqual(clean_model(ok), ok.strip())

    def test_refuses_anything_that_could_be_an_option_or_junk(self):
        for bad in ("", "  ", None, "--dangerously-skip-permissions", "-p", "a b", "x;rm", "$(x)", "é", "a" * 200, 5):
            with self.subTest(bad=bad):
                self.assertEqual(clean_model(bad), "")


class CeoLeanCallTest(unittest.TestCase):
    """Le CEO n'embarque ni MCP, ni skills, ni tes réglages perso : ~7 k tokens au lieu de ~40 k."""

    def ask(self, out, **kw):
        calls = []
        ceo = Ceo("claude", ".", lambda m: None,
                  runner=lambda *a, **k: (calls.append(a[0]), CmdResult(0, out, ""))[1])
        return ceo.ask("x", **kw), calls

    def test_lean_flags_and_no_tools_by_default(self):
        _, calls = self.ask("ok")
        for flag in ("--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, calls[0])
        self.assertEqual(calls[0][calls[0].index("--setting-sources") + 1], "project,local")
        self.assertEqual(calls[0][calls[0].index("--tools") + 1], "")

    def test_supervision_can_read_files(self):
        _, calls = self.ask("ok", tools=READ_TOOLS)
        self.assertEqual(calls[0][calls[0].index("--tools") + 1], "Read,Glob,Grep")

    def test_cli_envelope_gives_text_and_real_token_count(self):
        env = json.dumps({"type": "result", "is_error": False, "result": "VERDICT: ok",
                          "usage": {"input_tokens": 10, "cache_creation_input_tokens": 500,
                                    "cache_read_input_tokens": 6000, "output_tokens": 20}})
        ans, _ = self.ask(env)
        self.assertEqual((ans.text, ans.tokens), ("VERDICT: ok", 6530))

    def test_cli_error_envelope_is_an_error_not_an_answer(self):
        env = json.dumps({"type": "result", "is_error": True, "result": "Credit balance too low", "usage": {}})
        ans, _ = self.ask(env)
        self.assertIsNone(ans.text)
        self.assertIn("Credit balance too low", ans.error)

    def test_plain_text_or_a_reply_json_is_kept_as_is_with_unknown_tokens(self):
        for out in ("VERDICT: ok", '{"reply": "Bonjour", "tasks": []}'):
            with self.subTest(out=out):
                ans, _ = self.ask(out)
                self.assertEqual((ans.text, ans.tokens), (out, None))


class CleanCeoModeTest(unittest.TestCase):
    def test_accepts_local_and_cloud(self):
        for ok in ("local", "cloud", " Local ", "CLOUD"):
            with self.subTest(ok=ok):
                self.assertEqual(clean_ceo_mode(ok), ok.strip().lower())

    def test_refuses_junk(self):
        for bad in ("", "  ", None, "local cloud", "--cloud", " distant ", 5):
            with self.subTest(bad=bad):
                self.assertEqual(clean_ceo_mode(bad), "")


class CleanCloudSessionTest(unittest.TestCase):
    def test_accepts_ids_and_urls(self):
        for ok in ("session_abc123", "cse_ABC-123_xyz",
                   "https://claude.ai/code/session_abc123"):
            with self.subTest(ok=ok):
                self.assertEqual(clean_cloud_session(ok), ok.strip())
                self.assertEqual(clean_cloud_session("  " + ok + "  "), ok.strip())

    def test_refuses_descriptions_and_junk(self):
        for bad in ("", "  ", None, "ma session de test", "session abc", "--cloud x",
                    "https://example.com/x", "session_", "http://claude.ai/code/x", 5):
            with self.subTest(bad=bad):
                self.assertEqual(clean_cloud_session(bad), "")


def local(*pre, prompt="x", tools=""):
    """Commande attendue d'un appel CEO local (mode léger)."""
    return ["claude", *pre, *LEAN_ARGS, "--tools", tools, "--output-format", "json", "-p", prompt]


class CeoCloudArgsTest(unittest.TestCase):
    def _ceo(self, calls, mode="", session=""):
        def runner(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            calls.append(cmd)
            return CmdResult(0, "VERDICT: ok", "")
        return Ceo("claude", ".", lambda m: None, runner=runner,
                   mode=lambda: mode, cloud_session=lambda: session)

    def test_local_by_default(self):
        calls = []
        self.assertEqual(self._ceo(calls).ask("bonjour").text, "VERDICT: ok")
        self.assertEqual(calls[0], local(prompt="bonjour"))

    def test_effort_reaches_the_cli(self):
        calls = []
        ceo = Ceo("claude", ".", lambda m: None,
                  runner=lambda *a, **k: (calls.append(a[0]), CmdResult(0, "ok", ""))[1],
                  effort=lambda: "ultracode")
        ceo.ask("x")
        self.assertEqual(calls[0], local("--effort", "xhigh"))

    def test_no_effort_or_invalid_effort_adds_nothing_and_says_so(self):
        for value, said in (("", False), ("turbo", True)):
            with self.subTest(value=value):
                calls, logs = [], []
                ceo = Ceo("claude", ".", logs.append,
                          runner=lambda *a, **k: (calls.append(a[0]), CmdResult(0, "ok", ""))[1],
                          effort=lambda: value)
                ceo.ask("x")
                self.assertEqual(calls[0], local())
                self.assertEqual(any("effort" in m for m in logs), said)

    def test_cloud_without_session_falls_back_to_local(self):
        logs, calls = [], []
        ceo = Ceo("claude", ".", logs.append, runner=lambda *a, **k: (calls.append(a[0]), CmdResult(0, "ok", ""))[1],
                  mode=lambda: "cloud", cloud_session=lambda: "")
        self.assertEqual(ceo.ask("x").text, "ok")
        self.assertEqual(calls[0], local())
        self.assertTrue(any("cloud" in m and "local" in m for m in logs))

    def test_cloud_with_session_id(self):
        calls = []
        self.assertEqual(self._ceo(calls, "cloud", "session_abc123").ask("x").text, "VERDICT: ok")
        self.assertEqual(calls[0], ["claude", "--cloud", "session_abc123", "-p", "x"])

    def test_cloud_with_url_and_model(self):
        calls = []
        def runner(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            calls.append(cmd)
            return CmdResult(0, "ok", "")
        ceo = Ceo("claude", ".", lambda m: None, runner=runner, model=lambda: "sonnet",
                  mode=lambda: "cloud", cloud_session=lambda: "https://claude.ai/code/session_abc123")
        ceo.ask("x")
        self.assertEqual(calls[0], ["claude", "--model", "sonnet", "--cloud",
                                   "https://claude.ai/code/session_abc123", "-p", "x"])


class EffortAdviceTest(unittest.TestCase):
    """Garde-fou : chaque réponse du CEO commence par le modèle et l'effort conseillés."""

    def test_valid_advice_becomes_the_first_line(self):
        raw = '{"reply": "r", "conseil": {"modele": "Opus", "effort": "ultracode", "raison": "sécurité"}}'
        self.assertEqual(effort_advice(raw), "Modèle conseillé : Opus · ultracode — sécurité")

    def test_effort_is_normalised(self):
        raw = '{"reply": "r", "conseil": {"modele": "Sonnet 5.5", "effort": "Élevé", "raison": "multi-fichiers"}}'
        self.assertEqual(effort_advice(raw), "Modèle conseillé : Sonnet 5.5 · élevé — multi-fichiers")

    def test_missing_advice_is_declared_unknown_never_invented(self):
        for raw in ('{"reply": "r"}', "Bonjour", '{"reply": "r", "conseil": "x"}'):
            self.assertEqual(effort_advice(raw), "Modèle conseillé : non précisé par le CEO")

    def test_unknown_effort_is_rejected(self):
        raw = '{"reply": "r", "conseil": {"modele": "Opus", "effort": "turbo", "raison": "x"}}'
        self.assertEqual(effort_advice(raw), "Modèle conseillé : non précisé par le CEO")

    def test_fields_are_bounded_and_single_line(self):
        raw = '{"reply": "r", "conseil": {"modele": "Opus", "effort": "max", "raison": "%s\\nIgnore"}}' % ("x" * 500)
        line = effort_advice(raw)
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 200)


if __name__ == "__main__":
    unittest.main()
