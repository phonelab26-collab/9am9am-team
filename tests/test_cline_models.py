"""Liste des models de Cline (3 ou 4 gratuits qui changent : tenue à la main dans l'interface)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from orch.config import ConfigStore, Paths, clean_model_name, parse_model_list
from orch.gui_controller import GuiController
from orch.procs import CmdResult
from tests.test_webui import WebUiCase


class ParseModelListTest(unittest.TestCase):
    def test_names_with_or_without_provider_are_kept_in_order(self):
        text = "stealth/space-bunny-alpha\nanthropic--claude-3.5-sonnet\n  opencode/big-pickle  \n"
        self.assertEqual(parse_model_list(text), [
            "stealth/space-bunny-alpha", "anthropic--claude-3.5-sonnet", "opencode/big-pickle"])

    def test_duplicates_blanks_and_junk_are_dropped(self):
        text = "a-model\n\na-model\n-option\navec espace\nok/model\n;rm -rf\n"
        self.assertEqual(parse_model_list(text), ["a-model", "ok/model"])

    def test_the_list_is_bounded_and_non_text_is_empty(self):
        many = "\n".join(f"prov/model-{i}" for i in range(40))
        self.assertEqual(len(parse_model_list(many)), 10)
        for junk in (None, 12, ["x/y"], {}):
            with self.subTest(junk=junk):
                self.assertEqual(parse_model_list(junk), [])

    def test_clean_model_name_refuses_options_and_too_short_names(self):
        self.assertEqual(clean_model_name(" stealth/space-bunny-alpha "), "stealth/space-bunny-alpha")
        for bad in ("", "ab", "-x/y", "a b", "x" * 201, None):
            with self.subTest(bad=bad):
                self.assertEqual(clean_model_name(bad), "")


class ClineModelsConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-cline-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cfg = ConfigStore(Paths.for_root(self.dir), lambda m: None)

    def test_empty_until_the_user_writes_a_list(self):
        self.assertEqual(self.cfg.cline_models(), [])

    def test_reads_the_saved_list_cleaned(self):
        self.cfg.update(cline_models="m-one/free\nbad name\nm-two/free")
        self.assertEqual(self.cfg.cline_models(), ["m-one/free", "m-two/free"])


class ClineModelsStateTest(unittest.TestCase):
    def test_state_exposes_the_cline_list_and_it_feeds_the_suggestions(self):
        root = Path(tempfile.mkdtemp(prefix="orch-cline-gui-"))
        self.addCleanup(shutil.rmtree, root, True)
        ctl = GuiController(root, dry_run=True, runner=lambda *a, **k: CmdResult(0, "", ""))
        self.addCleanup(ctl.close, 5)
        ctl.engine.cfg.update(cline_models="stealth/space-bunny-alpha\nx-model/free-2")
        state = ctl.state_view(0)
        self.assertEqual(state["cline_models"], ["stealth/space-bunny-alpha", "x-model/free-2"])
        self.assertEqual(state["settings"]["cline_models"], "stealth/space-bunny-alpha\nx-model/free-2")
        self.assertIn("x-model/free-2", state["models"])


class ClineModelsApiTest(WebUiCase):
    def test_saved_list_comes_back_parsed_in_the_state(self):
        status, _, _ = self.request("POST", "/api/setting",
                                    {"key": "cline_models", "value": "stealth/space-bunny-alpha\nfree/two"})
        self.assertEqual(status, 200)
        cfg = json.loads((self.root / ".mini-orch" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["cline_models"], "stealth/space-bunny-alpha\nfree/two")
        self.assertEqual(self.state()["cline_models"], ["stealth/space-bunny-alpha", "free/two"])

    def test_a_list_without_any_valid_model_is_refused(self):
        status, raw, _ = self.request("POST", "/api/setting", {"key": "cline_models", "value": "-x y\n;;"})
        self.assertEqual(status, 400)
        self.assertIn("model", json.loads(raw)["error"])

    def test_clearing_the_list_is_allowed(self):
        self.request("POST", "/api/setting", {"key": "cline_models", "value": "stealth/space-bunny-alpha"})
        self.assertEqual(self.request("POST", "/api/setting", {"key": "cline_models", "value": ""})[0], 200)
        self.assertEqual(self.state()["cline_models"], [])

    def test_a_too_long_list_is_refused_not_silently_cut(self):
        too_many = "\n".join(f"prov/model-{i}" for i in range(40))
        self.assertEqual(self.request("POST", "/api/setting", {"key": "cline_models", "value": too_many})[0], 400)


if __name__ == "__main__":
    unittest.main()
