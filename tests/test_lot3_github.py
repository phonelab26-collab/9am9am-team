"""Lot 3, point 12 : GitHub — les vraies causes, pas de panne mise en cache, pas d'erreur cachée."""
import json
import unittest

from orch.procs import CmdResult
from tests.test_github import ACCOUNTS, ACCOUNTS_ONE, REPOS_A, REPOS_B, GithubReposTest

NOT_LOGGED = json.dumps({"hosts": {"github.com": [{"state": "error", "login": "x", "active": True}]}})


class Causes(GithubReposTest):
    def test_a_crash_shows_its_own_message(self):
        gh, _, _, _ = self.make([CmdResult(2, "", "panic: runtime error")])
        res = gh.list()
        self.assertFalse(res.ok)
        self.assertIn("panic", res.error)
        self.assertNotIn("n'est pas connecté", res.error)

    def test_a_timeout_says_timeout(self):
        gh, _, _, _ = self.make([CmdResult(124, "", "", timed_out=True)])
        self.assertIn("délai", gh.list().error)

    def test_unreadable_json_says_unreadable(self):
        gh, _, _, _ = self.make(["pas du json {"])
        res = gh.list()
        self.assertIn("illisible", res.error)
        self.assertNotIn("n'est pas connecté", res.error)

    def test_really_not_connected_still_says_so(self):
        for out in (NOT_LOGGED, json.dumps({"hosts": {}})):
            gh, _, _, _ = self.make([out])
            self.assertIn("gh auth login", gh.list().error)

    def test_gh_not_logged_in_exit_code_is_still_explained(self):
        gh, _, _, _ = self.make([CmdResult(1, "", "You are not logged into any GitHub hosts. Run gh auth login")])
        self.assertIn("gh auth login", gh.list().error)


class NoStickyFailure(GithubReposTest):
    def test_a_failure_is_not_cached(self):
        gh, calls, _, _ = self.make([CmdResult(2, "", "boom"), ACCOUNTS_ONE, REPOS_B])
        self.assertFalse(gh.list().ok)
        self.assertTrue(gh.list().ok)  # relancé tout de suite, sans attendre 300 s
        self.assertEqual(len(calls), 3)


class PartialSuccess(GithubReposTest):
    def test_the_failed_account_is_reported_as_a_warning(self):
        gh, _, _, _ = self.make([ACCOUNTS, REPOS_A, "tokB", CmdResult(1, "", "HTTP 401 Bad credentials")])
        res = gh.list()
        self.assertTrue(res.ok)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("phonelab26-collab", res.warnings[0])

    def test_a_clean_run_has_no_warning(self):
        gh, _, _, _ = self.make([ACCOUNTS_ONE, REPOS_B])
        self.assertEqual(gh.list().warnings, [])


class TokenOnlyWhenNeeded(GithubReposTest):
    def test_the_active_account_needs_no_token(self):
        gh, calls, _, envs = self.make([ACCOUNTS_ONE, REPOS_B])
        self.assertTrue(gh.list().ok)
        self.assertFalse(any(c[1:3] == ["auth", "token"] for c in calls))
        self.assertNotIn("GH_TOKEN", envs[-1])

    def test_only_inactive_accounts_load_a_token(self):
        gh, calls, _, _ = self.make([ACCOUNTS, REPOS_A, "tokB", REPOS_B])
        self.assertTrue(gh.list().ok)
        self.assertEqual(sum(1 for c in calls if c[1:3] == ["auth", "token"]), 1)


if __name__ == "__main__":
    unittest.main()
