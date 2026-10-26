"""Masquage des secrets avant qu'un texte d'agent soit affiché ou enregistré (chat.json)."""
import unittest

from orch.redact import redact


class RedactTest(unittest.TestCase):
    def check(self, cases):
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), expected)

    def test_sensitive_assignments_lose_their_value_quoted_or_not(self):
        self.check({
            "OPENAI_API_KEY=sk-live123 npm test": "OPENAI_API_KEY=*** npm test",
            'API_TOKEN="abc def" make': "API_TOKEN=*** make",
            "export DB_PASSWORD='p w' && run": "export DB_PASSWORD=*** && run",
        })

    def test_bearer_and_known_token_shapes_are_masked(self):
        self.check({
            'curl -H "Authorization: Bearer sk-live-abc" https://x': 'curl -H "Authorization: Bearer ***" https://x',
            "echo ghp_abcDEF123 github_pat_11AAA xoxb-123-abc sk-abc123": "echo *** *** *** ***",
            'curl -H "Authorization: Basic dXNlcjpwdw==" u': 'curl -H "Authorization: Basic ***" u',
        })

    def test_sensitive_flags_lose_their_value(self):
        self.check({
            "tool --api-key=abc123 --token xyz --password 'a b'": "tool --api-key=*** --token *** --password ***",
            "tool -p 8080 --key abc": "tool -p 8080 --key ***",
        })

    def test_credentials_inside_urls_are_masked(self):
        self.check({"git clone https://me:hunter2@github.com/o/r": "git clone https://me:***@github.com/o/r"})

    def test_ordinary_text_and_look_alikes_are_left_alone(self):
        for plain in ("python3 -m pytest tests/test_secrets.py -k TokenizerTests", "ls -la", "grep -r keyboard .",
                      "tool --keep-going foo", "tool --monkey-patch foo", "ssh user@host", "visit https://a.b/c",
                      "Le mot de passe est demandé à l'utilisateur."):
            with self.subTest(plain=plain):
                self.assertEqual(redact(plain), plain)

    def test_hostile_long_text_is_handled_in_linear_time(self):
        # Lot 1 : « a-a-a-… » coûtait 3,6 s pour 20 000 caractères (et ×4 à chaque doublement).
        import time
        for text in ("a-" * 50000, "a" * 100000, "x " * 50000 + "--" + "a-" * 30000,
                     "API_KEY" * 15000, "://" + "a" * 50000 + ":" + "b" * 50000):
            with self.subTest(text=text[:12]):
                start = time.monotonic()
                redact(text)
                self.assertLess(time.monotonic() - start, 0.5)

    def test_long_variable_names_are_still_masked(self):
        self.check({
            "SOME_VERY_LONG_PREFIX_FOR_THE_SERVICE_NAME_X_API_KEY=abcd1234 run":
                "SOME_VERY_LONG_PREFIX_FOR_THE_SERVICE_NAME_X_API_KEY=*** run",
            "API_KEY_" + "X" * 45 + "=abcd1234": "API_KEY_" + "X" * 45 + "=***",
        })

    def test_flags_glued_to_a_word_are_not_flags(self):
        self.assertEqual(redact("mot-de-passe-oublié reste"), "mot-de-passe-oublié reste")
        self.assertEqual(redact("abc--token xyz"), "abc--token xyz")

    def test_redaction_is_idempotent_and_total(self):
        once = redact("API_KEY=abc --token xyz Bearer qrs")
        self.assertEqual(redact(once), once)
        for junk in (None, 12, [], {}):
            with self.subTest(junk=junk):
                self.assertEqual(redact(junk), "")


if __name__ == "__main__":
    unittest.main()
