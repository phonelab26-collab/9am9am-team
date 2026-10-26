import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch import chat as chat_mod
from orch.chat import ChatStore, MAX_MESSAGES
from orch.config import Paths


class ChatStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="orch-chat-"))
        self.paths = Paths.for_root(self.dir)
        self.logs = []
        self.chat = ChatStore(self.paths, self.logs.append)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_add_assigns_increasing_ids_and_persists(self):
        a = self.chat.add("user", "Bonjour")
        b = self.chat.add("ceo", "Salut", kind="reply")
        self.assertEqual((a["id"], b["id"]), (1, 2))
        again = ChatStore(self.paths, self.logs.append).since(0)
        self.assertEqual([(m["role"], m["text"]) for m in again], [("user", "Bonjour"), ("ceo", "Salut")])
        self.assertTrue(again[0]["at"])
        self.assertEqual(self.paths.chat.stat().st_mode & 0o777, 0o600)

    def test_since_returns_only_newer_messages(self):
        for i in range(4):
            self.chat.add("user", f"m{i}")
        self.assertEqual([m["text"] for m in self.chat.since(2)], ["m2", "m3"])
        self.assertEqual(self.chat.since(99), [])

    def test_event_data_and_kind_kept(self):
        self.chat.add("event", "x", kind="task_start", data={"id": 3, "title": "t"})
        m = self.chat.since(0)[0]
        self.assertEqual((m["kind"], m["data"]), ("task_start", {"id": 3, "title": "t"}))

    def test_text_is_bounded_and_history_limited(self):
        self.chat.add("user", "x" * 20000)
        self.assertLessEqual(len(self.chat.since(0)[0]["text"]), 6000)
        self.assertGreaterEqual(MAX_MESSAGES, 100)  # garde-fou : plafond réel de production
        small = 20  # plafond réduit : 500 écritures + fsync coûtent ~18 s sur ce Mac
        with mock.patch.object(chat_mod, "MAX_MESSAGES", small):
            for _ in range(small + 10):
                self.chat.add("user", "m")
        msgs = self.chat.since(0)
        self.assertEqual(len(msgs), small)
        self.assertEqual(msgs[-1]["id"], small + 11)  # les ids continuent de croître

    def test_recent_keeps_only_conversation_roles(self):
        self.chat.add("user", "q")
        self.chat.add("event", "bruit", kind="task_start")
        self.chat.add("ceo", "r")
        self.assertEqual([m["text"] for m in self.chat.recent(10)], ["q", "r"])
        self.assertEqual([m["text"] for m in self.chat.recent(1)], ["r"])

    def test_unreadable_file_is_kept_aside_and_reading_never_crashes(self):
        self.paths.state.mkdir(parents=True)
        self.paths.chat.write_text("{oops", encoding="utf-8")
        self.assertEqual(self.chat.since(0), [])
        self.chat.add("user", "après")
        self.assertEqual(len(self.chat.since(0)), 1)
        self.assertEqual(len(list(self.paths.state.glob("chat.json.corrupt-*"))), 1)


if __name__ == "__main__":
    unittest.main()
