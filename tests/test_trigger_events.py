import os
import tempfile
import unittest
from datetime import datetime, timezone

from cicdanalysis.app import App
from cicdanalysis.config import Config
from cicdanalysis.db import Database
from cicdanalysis.jenkins import infer_job
from cicdanalysis.reports import Reports


def build(number=101, queue_id=32727, user_id=None):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "build_number": number,
        "build_url": f"https://jenkins.example/job/statistics-testa/{number}/",
        "queue_id": queue_id,
        "trigger_user_id": user_id,
        "trigger_source": "UNKNOWN",
        "branch": "main",
        "commit_sha": "03e872e75449",
        "commit_message": "Merge branch fix/UG-2701 into main",
        "agent_name": "eks-agent1",
        "image_ref": "harbor.example/ugame/statistics:test",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 61,
        "result": "SUCCESS",
        "building": False,
        "raw_summary": "{}",
    }


class TriggerEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.db = Database(self.path)
        self.db.init()
        self.job_id = self.db.upsert_job(infer_job("statistics-testa", "https://jenkins.example/job/statistics-testa/"))

    def tearDown(self):
        self.tmp.cleanup()

    def event(self):
        return {
            "event_id": "telegram-32727",
            "build_number": 101,
            "queue_id": 32727,
            "telegram_chat_id": "-1003919548725",
            "branch": "main",
            "service_type": "standalone",
            "service_name": "statistics",
            "namespace": "testa",
            "environment": "testa",
            "status": "SUCCESS",
            "http_status": 201,
            "queue_url": "https://jenkins.example/queue/item/32727/",
            "build_url": "https://jenkins.example/job/statistics-testa/101/",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload": "{}",
        }

    def test_trigger_before_build_is_linked_and_survives_repoll(self):
        mars = self.db.upsert_user("telegram:8601", "Mars Stephen", "marsstephen", "8601")
        trigger_id, matched = self.db.save_trigger_event(self.job_id, mars, self.event())
        self.assertGreater(trigger_id, 0)
        self.assertIsNone(matched)

        auto = self.db.upsert_user("jenkins-service", "Jenkins Service")
        build_id, _ = self.db.save_build(self.job_id, build(user_id=auto))
        self.assertTrue(self.db.apply_trigger_event(self.job_id, build_id, 101, 32727))

        # A later Jenkins poll must not replace the authoritative panel user.
        self.db.save_build(self.job_id, build(user_id=auto))
        row = self.db.query("""SELECT u.display_name,u.telegram_username,b.trigger_source
          FROM builds b JOIN users u ON u.id=b.trigger_user_id WHERE b.id=?""", (build_id,))[0]
        self.assertEqual(row["display_name"], "Mars Stephen")
        self.assertEqual(row["telegram_username"], "marsstephen")
        self.assertEqual(row["trigger_source"], "TELEGRAM_PANEL")

    def test_trigger_after_build_repairs_existing_automatic_record(self):
        build_id, _ = self.db.save_build(self.job_id, build())
        mars = self.db.upsert_user("telegram:marsstephen", "Mars Stephen", "marsstephen")
        _, matched = self.db.save_trigger_event(self.job_id, mars, self.event())
        self.assertEqual(matched, build_id)
        report = Reports(self.db, "Asia/Kuala_Lumpur").overview_days(1, "testa")
        self.assertEqual(report["users"][0]["name"], "Mars Stephen")
        self.assertEqual(report["users"][0]["telegram_username"], "marsstephen")

    def test_app_parses_build_and_queue_ids_from_urls(self):
        app = App(Config(database_path=os.path.join(self.tmp.name, "app.db")))
        result = app.record_trigger({
            "event_id": "panel-request-1",
            "trigger_name": "Mars Stephen",
            "telegram_username": "@marsstephen",
            "telegram_user_id": "8601",
            "job_name": "statistics-testa",
            "branch": "main",
            "environment": "testa",
            "namespace": "testa",
            "triggered_at": "2026-09-07T15:57:17+08:00",
            "queue_url": "https://jenkins.example/queue/item/32727/",
            "build_url": "https://jenkins.example/job/statistics-testa/101/",
        })
        self.assertEqual(result["queue_id"], 32727)
        self.assertEqual(result["build_number"], 101)
        self.assertEqual(result["telegram_username"], "marsstephen")

    def test_multiple_developers_are_counted_separately(self):
        mars = self.db.upsert_user("telegram:8601", "Mars Stephen", "marsstephen", "8601")
        mew = self.db.upsert_user("telegram:8602", "Mew", "slo_dream_03", "8602")
        first = self.event()
        self.db.save_trigger_event(self.job_id, mars, first)
        first_id, _ = self.db.save_build(self.job_id, build(number=101, queue_id=32727))
        self.db.apply_trigger_event(self.job_id, first_id, 101, 32727)
        second = {**self.event(), "event_id": "telegram-32728", "build_number": 102, "queue_id": 32728}
        self.db.save_trigger_event(self.job_id, mew, second)
        second_id, _ = self.db.save_build(self.job_id, build(number=102, queue_id=32728))
        self.db.apply_trigger_event(self.job_id, second_id, 102, 32728)

        report = Reports(self.db, "Asia/Kuala_Lumpur").overview_days(1, "testa")
        self.assertEqual(report["developer_count"], 2)
        self.assertEqual(report["panel_triggered"], 2)
        self.assertEqual({r["name"] for r in report["users"]}, {"Mars Stephen", "Mew"})


if __name__ == "__main__":
    unittest.main()
