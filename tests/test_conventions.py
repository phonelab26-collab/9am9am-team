"""Conventions vérifiées par le code lui-même (AST + fichiers) — les règles ne
vivent pas seulement dans la doc : une violation casse la suite."""
import ast
import re
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1] / "orch"
NUMERIC = (int, float)


def _rounded_corners(css: str):
    """Valeurs de border-radius autres que « 0 » (le design interdit les arrondis)."""
    for m in re.finditer(r"border-radius\s*:\s*([^;}]+)", css):
        value = m.group(1).strip()
        if not re.fullmatch(r"0(?:px)?(?:\s+0(?:px)?){0,3}", value):
            yield value


def _numeric_keywords(path: Path, keywords=("timeout", "poll_interval")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg in keywords and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, NUMERIC):
                yield f"{path.name}:{kw.lineno} {kw.arg}={kw.value.value}"


class NoMagicTimeoutTest(unittest.TestCase):
    def test_no_numeric_timeout_in_the_product_code(self):
        # Un délai en dur = une valeur qu'on ne retrouve ni dans l'audit ni dans
        # la tête du module. Toute attente passe par une constante nommée.
        offenders = []
        for path in sorted(ORCH.rglob("*.py")):
            offenders += list(_numeric_keywords(path))
        self.assertEqual(
            offenders, [],
            "délais écrits en dur (nomme une constante en tête de module) : "
            + ", ".join(offenders))

    def test_rule_actually_catches_a_literal(self):
        # Le filet doit lui-même être vérifié : une source contenant timeout=9
        # doit être signalée.
        src = "def f():\n    g(x, timeout=9)\n"
        fake = Path(__file__).with_name("_sniff_source.py")
        try:
            fake.write_text(src, encoding="utf-8")
            self.assertEqual(list(_numeric_keywords(fake)), ["_sniff_source.py:2 timeout=9"])
        finally:
            fake.unlink(missing_ok=True)


class NoRoundedCornerTest(unittest.TestCase):
    """« Semi rétro minimaliste, pas d'arrondi » : la règle est dans le CSS,
    mais rien ne dit qu'elle y restera — ce test si."""

    def test_no_border_radius_above_zero(self):
        css = (ORCH / "web" / "app.css").read_text(encoding="utf-8")
        bad = list(_rounded_corners(css))
        self.assertEqual(bad, [], "arrondi trouvé dans app.css : " + ", ".join(bad))

    def test_rule_actually_catches_a_radius(self):
        # Le filet doit lui-même être vérifié.
        self.assertEqual(list(_rounded_corners("a { border-radius: 8px 8px 0 0; }")), ["8px 8px 0 0"])
        self.assertEqual(list(_rounded_corners("a { border-radius: 0; }")), [])


class EffortSettingsWiredTest(unittest.TestCase):
    """Chaque réglage d'effort accepté par le serveur a sa liste dans la page et son
    branchement dans app.js — sinon un réglage existe mais l'utilisateur n'a pas la main."""

    def test_every_effort_key_has_a_select_and_is_saved_by_the_page(self):
        from orch.webui import SETTINGS
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        keys = [k for k in SETTINGS if k.endswith("_effort")]
        self.assertEqual(sorted(keys), ["autoclaw_effort", "ceo_effort", "cline_effort"])
        for key in keys:
            with self.subTest(key=key):
                self.assertRegex(html, rf'<select id="{key}" class="effort"')
                self.assertIn(f'"{key}"', js)
        self.assertRegex(html, r'<select id="ca-effort" class="effort"')

    def test_page_levels_match_the_server_levels(self):
        from orch.effort import LEVELS
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        m = re.search(r"const EFFORTS = \[([^\]]*)\]", js)
        self.assertIsNotNone(m)
        self.assertEqual(tuple(re.findall(r'"([^"]*)"', m.group(1))), ("",) + LEVELS)


class CeoModelPickerTest(unittest.TestCase):
    """Le model du CEO se choisit dans une liste (une zone de saisie à suggestions ne propose
    plus que ce qui ressemble à la valeur déjà écrite : « sonnet » masquait opus et haiku)."""

    def test_page_offers_every_suggested_model_in_a_real_list(self):
        from orch.ceo import CEO_MODEL_SUGGESTIONS
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<select id="ceo_model_pick"')
        self.assertIn("ceo_model_pick", js)
        m = re.search(r"const CEO_MODEL_CHOICES = \[([^\]]*)\]", js)
        self.assertIsNotNone(m)
        offered = re.findall(r'"([^"]*)"', m.group(1))
        self.assertEqual(offered[0], "")  # défaut de Claude Code
        self.assertEqual(offered[1:], list(CEO_MODEL_SUGGESTIONS))
        self.assertGreaterEqual(len(CEO_MODEL_SUGGESTIONS), 3)


class CopyMessageWiredTest(unittest.TestCase):
    """Chaque message de la conversation (le tien comme la réponse du CEO) a un bouton
    « Copier » ; un échec de copie est dit, jamais avalé."""

    def js(self):
        return (ORCH / "web" / "app.js").read_text(encoding="utf-8")

    def test_every_message_gets_a_copy_button_with_the_text_shown(self):
        js = self.js()
        node = re.search(r"function messageNode\(m\) \{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(node)
        body = node.group(1)
        # un bouton sur ton message ET sur celui du CEO, qui copient le texte source (Markdown brut)
        self.assertEqual(body.count("copyButton(m.text)"), 2)
        self.assertEqual(body.count("copyButton("), 2)

    def test_copy_uses_the_clipboard_and_reports_a_failure(self):
        js = self.js()
        copy = re.search(r"async function copyText\(.*?\n\}\n", js, re.S)
        self.assertIsNotNone(copy)
        self.assertIn("navigator.clipboard.writeText", copy.group(0))
        # Le logiciel affiche la page dans un WKWebView, où l'API moderne peut être refusée :
        # la copie de secours passe par execCommand, et seul l'échec des deux est dit.
        self.assertIn("execCommand", self.js())
        self.assertIn("Copie impossible", copy.group(0))

    def test_copy_label_is_a_constant_so_a_double_click_cannot_freeze_it(self):
        js = self.js()
        self.assertNotIn("const label = button.textContent", js)
        self.assertIn("COPY_LABEL", js)

    def test_step_icons_are_read_without_touching_the_prototype(self):
        self.assertIn("Object.hasOwn(STEP_ICON", self.js())

    def test_copy_button_is_styled_without_rounded_corners(self):
        css = (ORCH / "web" / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.copy\b[^{]*\{")


class ReplayWiredTest(unittest.TestCase):
    """« ↻ Rejouer » renvoie un prompt qui n'a pas abouti sans le retaper : sur chacun de tes
    messages, et sur une réponse d'erreur du CEO (qui rejoue le message juste avant)."""

    def js(self):
        return (ORCH / "web" / "app.js").read_text(encoding="utf-8")

    def fn(self, name):
        found = re.search(r"(?:async )?function " + name + r"\(.*?\) \{(.*?)\n\}\n", self.js(), re.S)
        self.assertIsNotNone(found, name)
        return found.group(1)

    def test_user_messages_and_error_replies_get_a_replay_button(self):
        node = self.fn("messageNode")
        self.assertEqual(node.count("replayButton("), 2)
        self.assertIn("replayButton(m.text)", node)          # ton message : lui-même
        self.assertIn('kind === "error"', node)
        self.assertIn("replayButton(lastUserText)", node)    # une erreur du CEO : ton message d'avant

    def test_the_page_remembers_the_last_user_message_while_rendering(self):
        self.assertIn("lastUserText = m.text", self.fn("renderChat"))

    def test_replay_resends_the_text_alone_and_never_while_the_ceo_works(self):
        replay = self.fn("replayPrompt")
        self.assertIn("if (busy)", replay)
        self.assertIn("if (replaying)", replay)                       # un double clic n'envoie qu'une fois
        self.assertIn("send(text, { files: [], pasted: [] })", replay)  # jamais les fichiers joints du moment

    def test_replay_button_is_styled(self):
        css = (ORCH / "web" / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.replay\b[^{]*\{")


class FormattedAnswersWiredTest(unittest.TestCase):
    """Les réponses sont mises en forme (titres, listes, code) et l'activité des agents est
    détaillée (« a utilisé 4 outils », « arrêtée ») — sans jamais injecter de HTML."""

    def read(self, name):
        return (ORCH / "web" / name).read_text(encoding="utf-8")

    def test_markdown_module_is_served_and_loaded_before_the_app(self):
        from orch.webui import STATIC
        self.assertIn("/markdown.js", STATIC)
        html = self.read("index.html")
        self.assertLess(html.index('src="markdown.js"'), html.index('src="app.js"'))

    def test_ceo_and_worker_answers_go_through_the_markdown_renderer(self):
        js = self.read("app.js")
        self.assertIn("Markdown.parseMarkdown", js)
        node = re.search(r"function messageNode\(m\) \{(.*?)\n\}\n", js, re.S).group(1)
        self.assertIn("renderMarkdown(m.text)", node)
        result = re.search(r"function taskResult\(m\) \{(.*?)\n\}\n", js, re.S).group(1)
        self.assertIn("renderMarkdown(m.text)", result)

    def test_nothing_ever_injects_html(self):
        for name in ("app.js", "markdown.js"):
            with self.subTest(file=name):
                src = self.read(name)
                for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
                    self.assertNotIn(banned, src)

    def test_page_shows_the_tools_an_agent_used_and_that_it_was_stopped(self):
        js = self.read("app.js")
        result = re.search(r"function taskResult\(m\) \{(.*?)\n\}\n", js, re.S).group(1)
        self.assertIn("stepsNode(d)", result)
        self.assertIn("d.cancelled", result)
        steps = re.search(r"function stepsNode\(d\) \{(.*?)\n\}\n", js, re.S).group(1)
        self.assertIn("A utilisé", steps)
        self.assertIn("steps_omitted", steps)

    def test_the_model_advice_line_is_set_apart_from_the_answer(self):
        self.assertIn("md-advice", self.read("app.js"))
        self.assertRegex(self.read("app.css"), r"\.md-advice\b[^{]*\{")

    def test_styles_exist_for_rendered_text_and_steps(self):
        css = self.read("app.css")
        for selector in (r"\.md\b", r"\.ev-steps\b", r"\.step\b"):
            with self.subTest(selector=selector):
                self.assertRegex(css, selector + r"[^{]*\{")


class FreeModelPickerTest(unittest.TestCase):
    """Le model d'opencode se choisit dans une vraie liste remplie par le scan d'ouverture."""

    def test_page_lists_the_scanned_free_models_and_can_rescan(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<select id="opencode_model_pick"')
        self.assertIn('id="free-models-note"', html)
        self.assertIn('id="free-models-rescan"', html)
        self.assertIn("renderFreeModels(state.free_models)", js)
        self.assertIn('"/api/free_models_scan"', js)


class ClineModelPickerTest(unittest.TestCase):
    """Cline ne publie aucun catalogue : ses 3 ou 4 models gratuits se notent à la main et
    remplissent un menu — la page, le réglage et le rendu doivent rester branchés ensemble."""

    def test_page_has_the_menu_the_list_field_and_the_rendering(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<select id="cline_model_pick"')
        self.assertRegex(html, r'<textarea id="cline_models"')
        self.assertIn('"cline_models"', js)
        self.assertIn("renderClineModels(state.cline_models)", js)

    def test_the_list_setting_is_accepted_by_the_server(self):
        from orch.webui import SETTINGS
        self.assertIn("cline_models", SETTINGS)


class RetroFontTest(unittest.TestCase):
    """Police des Macintosh des années 90 : Monaco d'abord, trait fin (pas de gras épais)."""

    def css(self):
        return (ORCH / "web" / "app.css").read_text(encoding="utf-8")

    def test_monaco_comes_first_in_the_font_stack(self):
        m = re.search(r"--mono:\s*([^;]+);", self.css())
        self.assertIsNotNone(m)
        self.assertTrue(m.group(1).strip().startswith("Monaco"), m.group(1))
        self.assertIn("monospace", m.group(1))  # repli générique hors macOS

    def test_no_heavy_font_weights(self):
        heavy = re.findall(r"font-weight:\s*(?:[6-9]00|bold)", self.css())
        self.assertEqual(heavy, [], "gras épais trouvé : le style rétro reste fin")


class DatalistsExistTest(unittest.TestCase):
    """renderSettings remplit des <datalist> désignées par un id passé en variable : un id
    retiré du HTML ferait planter toute la page (réglages jamais synchronisés)."""

    def test_every_list_filled_by_render_settings_exists_in_the_page(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        loop = re.search(r"for \(const \[listId, values\] of \[(.*?)\]\) \{", js)
        self.assertIsNotNone(loop)
        ids = re.findall(r'\["([a-z_]+)",', loop.group(1))
        self.assertTrue(ids)
        for list_id in ids:
            with self.subTest(list=list_id):
                self.assertIn(f'<datalist id="{list_id}"', html)


class WorkspaceButtonsTest(unittest.TestCase):
    """« Parcourir… » choisit le dossier de travail ; « + » joint des fichiers."""

    def test_browse_picks_the_workspace_and_plus_attaches_files(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<button type="button" id="ws-browse"[^>]*>📂 Parcourir…</button>')
        self.assertRegex(html, r'<button type="button" id="ws-add-file"[^>]*>\+</button>')
        self.assertIn('"/api/workspace_pick"', js)
        self.assertRegex(js, r'\$\("ws-add-file"\)\.addEventListener\("click", \(\) => \$\("ws-pick"\)\.click\(\)\)')


class VerifyCommandWiredTest(unittest.TestCase):
    def test_settings_offer_the_verification_command(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'<input id="verify_cmd"')
        self.assertIn('id="verify-hint"', html)
        self.assertIn('"verify_cmd"', js)
        self.assertIn("verify_effective", js)  # la commande détectée est montrée à l'utilisateur


class UsageLineWiredTest(unittest.TestCase):
    def test_topbar_has_the_usage_line_and_the_page_fills_it(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(html, r'id="usage"')
        self.assertIn("/api/usage", js)


class NoInlineStyleTest(unittest.TestCase):
    """CSP : style-src 'self' sans unsafe-inline — un attribut style écrit en dur
    dans le HTML serait bloqué par le navigateur (et c'est aussi une injection CSS)."""

    def test_index_html_carries_no_style_attribute(self):
        html = (ORCH / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('style="', html)

    def test_app_js_uses_cssom_only(self):
        js = (ORCH / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('setAttribute("style"', js)
        self.assertNotIn("cssText", js)


if __name__ == "__main__":
    unittest.main()
