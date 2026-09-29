import os
import tempfile
import unittest
from datetime import datetime, timezone

from cicdanalysis.app import App
from cicdanalysis.config import Config
from cicdanalysis.jenkins import infer_job
from cicdanalysis.userbot import UserAccountListener, to_update
from test_release_groups import PROD_FAILED, UAT, UAT_RESULT, UAT_TRIGGER, build


class Message:
    def __init__(self, id, text, when, sender="uguatdeploybot"):
        self.id, self.message, self.date, self.sender = id, text, when, sender
    async def get_sender(self):
        return type("Sender", (), {"username": self.sender})()


class FakeClient:
    """Enough of telethon.TelegramClient for backfill()."""
    def __init__(self, history): self.history = history
    async def connect(self): pass
    async def disconnect(self): pass
    async def is_user_authorized(self): return True
    async def get_dialogs(self): return []
    async def iter_messages(self, chat_id, offset_date=None, reverse=False):
        for message in self.history.get(chat_id, []):
            if message.date >= offset_date: yield message


class UserAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(database_path=os.path.join(self.tmp.name, "a.db"), database_url="",
                             telegram_user_api_id="1", telegram_user_api_hash="h",
                             telegram_user_trusted_senders="ugopsbot,@uguatdeploybot,ugprodopsbot")
        self.app = App(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_basic_group_ids_are_not_used_for_replies(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(to_update(int(UAT), Message(5, "x", now))["message"]["reply_unsafe"])
        self.assertFalse(to_update(-1003412281586, Message(5, "x", now))["message"]["reply_unsafe"])
        result = self.app.handle_update(to_update(int(UAT), Message(9, UAT_RESULT, now)))
        self.assertEqual(result["build_number"], 228)
        stored = self.app.db.query("SELECT telegram_message_id FROM release_results")[0]
        self.assertIsNone(stored["telegram_message_id"])

    def test_backfill_attributes_already_collected_builds(self):
        job_id = self.app.db.upsert_job(infer_job("uat-activity-rpc-prod"))
        self.app.db.save_build(job_id, build(228, "2026-09-28T13:02:09+00:00"))
        self.app.db.save_build(job_id, build(227, "2026-09-27T03:00:00+00:00"))
        at = datetime(2026, 9, 28, 13, 2, tzinfo=timezone.utc)
        history = {int(UAT): [Message(1, UAT_TRIGGER, at), Message(2, "闲聊", at, "someone"),
                              Message(3, UAT_TRIGGER.replace("Infi", "Fake"), at, "someone")]}
        listener = UserAccountListener(self.app)
        listener._client = lambda: FakeClient(history)
        listener.backfill(36500)
        rows = self.app.db.query("""SELECT b.build_number,u.display_name FROM builds b
          LEFT JOIN users u ON u.id=b.trigger_user_id ORDER BY b.build_number""")
        self.assertEqual([(r["build_number"], r["display_name"]) for r in rows], [(227, None), (228, "Infi")])
        # Messages from anyone but the release bots are ignored.
        self.assertEqual(len(self.app.db.query("SELECT id FROM trigger_events")), 1)


if __name__ == "__main__":
    unittest.main()


class RunningListenerBackfillTests(unittest.TestCase):
    def test_backfill_runs_on_the_live_connection(self):
        import asyncio, threading
        with tempfile.TemporaryDirectory() as folder:
            config = Config(database_path=os.path.join(folder, "a.db"), database_url="",
                            telegram_user_api_id="1", telegram_user_api_hash="h")
            app = App(config)
            with self.assertRaises(ValueError):
                app.telegram_backfill({"days": 14})  # listener not connected
            now = datetime.now(timezone.utc)
            listener = app.user_listener
            listener._client = lambda: self.fail("must not open a second client while running")
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, daemon=True); thread.start()
            try:
                listener.client, listener.loop = FakeClient({int(UAT): [Message(1, UAT_TRIGGER, now)]}), loop
                stats = app.telegram_backfill({"days": 14})
                self.assertEqual((stats["messages"], stats["handled"]), (1, 1))
                with self.assertRaises(ValueError):
                    app.telegram_backfill({"days": 0})
            finally:
                loop.call_soon_threadsafe(loop.stop); thread.join(5); loop.close()
