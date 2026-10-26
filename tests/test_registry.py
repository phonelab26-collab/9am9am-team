"""Le registry est la seule déclaration des workers : ces tests garantissent que
les consommateurs (config, webui) restent en syntonie avec lui."""
import unittest

from orch.config import BUILTIN_WORKERS
from orch.registry import DEFAULT, NAMES, SPECS, setting_keys, shell_names, spec_for
from orch.webui import SETTINGS


class RegistryCohesionTest(unittest.TestCase):
    def test_builtin_workers_derive_from_registry(self):
        self.assertEqual(BUILTIN_WORKERS, NAMES)

    def test_default_worker_exists_and_is_structured(self):
        self.assertIn(DEFAULT, NAMES)
        self.assertFalse(spec_for(DEFAULT).is_shell,
                         "le worker de repli doit être structured (pas de shell muet en secours)")

    def test_settings_whitelist_covers_every_worker_key(self):
        # Une clé worker oubliée ici = un réglage invisible pour l'API : 400 silencieux.
        missing = [k for k in setting_keys() if k not in SETTINGS]
        self.assertEqual(missing, [], f"clés workers absentes de SETTINGS : {missing}")

    def test_no_duplicate_settings(self):
        self.assertEqual(len(SETTINGS), len(set(SETTINGS)))

    def test_shell_spec_keys_are_consistent(self):
        for spec in SPECS:
            if spec.is_shell:
                self.assertEqual(spec.config_cmd_key, f"{spec.name}_cmd")
                self.assertIn(f"{spec.name}_effort", spec.setting_keys)
                self.assertEqual(spec.cmd_env, f"MINI_ORCH_{spec.name.upper()}_CMD")

    def test_shell_names_match_the_specs(self):
        self.assertEqual(shell_names(),
                         tuple(s.name for s in SPECS if s.is_shell))
        self.assertTrue(set(shell_names()) <= set(NAMES))


if __name__ == "__main__":
    unittest.main()
