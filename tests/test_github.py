import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from orch.config import ConfigStore, Paths
from orch.github import GithubRepos, clean_repo
from orch.procs import CmdResult

ACCOUNTS = json.dumps({"hosts": {"github.com": [
    {"state": "success", "login": "alice", "active": True},
    {"state": "success", "login": "phonelab26-collab", "active": False},
]}})
ACCOUNTS_ONE = json.dumps({"hosts": {"github.com": [
    {"state": "success", "login": "phonelab26-collab", "active": True}]}})
ACCOUNTS_OTHER = json.dumps({"hosts": {"github.com": [
    {"state": "success", "login": "phonelab26-collab", "active": False}]}})
REPOS_A = json.dumps([{"nameWithOwner": "alice/site", "isPrivate": False, "updated": "2026-09-20"}])
REPOS_B = json.dumps([
    {"nameWithOwner": "phonelab26-collab/9am9am-team", "isPrivate": False, "updated": "2026-09-28"},
    {"nameWithOwner": "phonelab26-collab/kino", "isPrivate": True, "updated": "2026-09-01"},
    {"nameWithOwner": "junk/in valide", "isPrivate": False, "updated": ""},  # écarté
    "not-a-dict",
])


class CleanRepoTest(unittest.TestCase):
    def test_accepts_owner_repo(self):
        for ok in (" phonelab26-collab/9am9am-team ", "a/b", "x.y_z/w-1.2"):
            with self.subTest(ok=ok):
                self.assertEqual(clean_repo(ok), ok.strip())

    def test_refuses_junk(self):
        for bad in ("", "  ", None, "pas-de-slash", "/repo", "owner/", "a b/c d",
                    "--cloud x", "https://github.com/a/b", "a" * 60 + "/" + "b" * 120, 5):
            with self.subTest(bad=bad):
                self.assertEqual(clean_repo(bad), "")


class GithubReposTest(unittest.TestCase):
    """Séquence réelle : `gh auth status` → par compte `gh auth token` puis `gh repo list`."""

    def make(self, results, gh_bin="/fake/gh"):
        calls, logs, envs = [], [], []

        def runner(cmd, timeout, cwd=None, cancel=None, on_spawn=None, env=None):
            calls.append(cmd)
            envs.append(dict(env or {}))
            out = results.pop(0)
            return out if isinstance(out, CmdResult) else CmdResult(0, out, "")

        gh = GithubRepos(logs.append, runner=runner, gh_bin=gh_bin)
        return gh, calls, logs, envs

    def test_merges_all_logged_in_accounts(self):
        gh, calls, _, _ = self.make([ACCOUNTS, REPOS_A, "tokB", REPOS_B])
        res = gh.list()
        self.assertTrue(res.ok)
        self.assertEqual([r.full_name for r in res.repos],
                         ["alice/site", "phonelab26-collab/9am9am-team",
                          "phonelab26-collab/kino"])
        self.assertFalse(res.repos[1].private)
        self.assertTrue(res.repos[2].private)
        self.assertEqual(calls[0][1:3], ["auth", "status"])
        self.assertTrue(any("repo" in c for c in calls))

    def test_token_goes_only_to_the_subprocess_env(self):
        gh, _, logs, envs = self.make([ACCOUNTS_OTHER, "gho_SECRET_TOKEN", REPOS_B])
        res = gh.list()
        self.assertTrue(res.ok)
        self.assertEqual(envs[-1].get("GH_TOKEN"), "gho_SECRET_TOKEN")
        self.assertFalse(any("gho_SECRET" in line for line in logs))

    def test_cache_avoids_second_call(self):
        script = [ACCOUNTS, REPOS_A, "tokB", REPOS_B]
        gh, calls, _, _ = self.make(script + script)
        gh.list()
        gh.list()
        self.assertEqual(len(calls), 4)
        gh.list(force=True)
        self.assertEqual(len(calls), 8)

    def test_missing_gh_is_explicit(self):
        gh, calls, _, _ = self.make([], gh_bin="")
        self.assertFalse(gh.available())
        res = gh.list()
        self.assertFalse(res.ok)
        self.assertIn("gh", res.error)
        self.assertEqual(calls, [])

    def test_not_logged_in_is_explained(self):
        gh, _, _, _ = self.make([CmdResult(1, "", "gh: To get started with GitHub CLI, please run: gh auth login")])
        res = gh.list()
        self.assertFalse(res.ok)
        self.assertIn("gh auth login", res.error)

    def test_bad_json_is_reported(self):
        gh, _, _, _ = self.make([ACCOUNTS_ONE, "pas du json {"])
        res = gh.list()
        self.assertFalse(res.ok)
        self.assertIn("illisible", res.error)

    def test_one_account_failing_still_returns_the_others(self):
        gh, _, logs, _ = self.make([ACCOUNTS, CmdResult(1, "", "HTTP 401"), "tokB", REPOS_B])
        res = gh.list()
        self.assertTrue(res.ok)
        self.assertEqual(len(res.repos), 2)
        self.assertTrue(any("alice" in line for line in logs))

    def test_hung_gh_on_auth_status_gives_a_clear_error(self):
        # gh qui pend jusqu'au timeout : jamais de crash, jamais d'attente infinie.
        gh, _, _, _ = self.make([CmdResult(124, "", "", timed_out=True)])
        res = gh.list()
        self.assertFalse(res.ok)
        self.assertTrue(res.error)

    def test_two_concurrent_callers_do_one_single_gh_journey(self):
        # Le fetch est sous verrou : 2 threads simultanés = 1 seul parcours gh
        # (5 appels), pas 2× le gaspillage de rate-limit GitHub.
        script = [ACCOUNTS, REPOS_A, "tokB", REPOS_B]
        gh, calls, _, _ = self.make(script + script)  # 2e moitié inutilisée si le lock tient
        results = [None, None]
        barrier = threading.Barrier(2)

        def call(i):
            barrier.wait(timeout=5)  # départ simultané
            results[i] = gh.list()

        threads = [threading.Thread(target=call, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertTrue(results[0].ok and results[1].ok)
        self.assertEqual([r.full_name for r in results[0].repos],
                         [r.full_name for r in results[1].repos])
        self.assertEqual(len(calls), 4, "un seul parcours gh pour deux appels simultanés")

    def test_hung_gh_on_repo_list_keeps_other_accounts(self):
        gh, _, logs, _ = self.make([ACCOUNTS,
                                    CmdResult(124, "", "", timed_out=True),  # repo list de A
                                    "tokB", REPOS_B])
        res = gh.list()
        self.assertTrue(res.ok, res.error)
        self.assertEqual([r.full_name for r in res.repos],
                         ["phonelab26-collab/9am9am-team", "phonelab26-collab/kino"])
        self.assertTrue(logs, "le compte en timeout doit être journalisé")


class CeoCloudRepoConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-gh-"))
        self.cfg = ConfigStore(Paths.for_root(self.dir), lambda m: None)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_default_empty_and_persisted(self):
        self.assertEqual(self.cfg.ceo_cloud_repo(), "")
        self.cfg.update(ceo_cloud_repo="phonelab26-collab/9am9am-team")
        self.assertEqual(self.cfg.ceo_cloud_repo(), "phonelab26-collab/9am9am-team")
        self.cfg.update(ceo_cloud_repo=5)
        self.assertEqual(self.cfg.ceo_cloud_repo(), "")


if __name__ == "__main__":
    unittest.main()