import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from cicdanalysis.analyzer import analyze
from cicdanalysis.app import App
from cicdanalysis.commands import parse_command
from cicdanalysis.config import Config
from cicdanalysis.jenkins import infer_job

TESTA, UAT = "-1003919548725", "-5592801576"


def cmd(text, chat=TESTA, message_id=900):
    return {"update_id": 1, "message": {"message_id": message_id, "chat": {"id": int(chat)}, "text": text,
                                        "from": {"username": "xuanhan"}}}


def build(number, at, result="FAILURE"):
    return {"build_number": number, "build_url": f"https://j/job/x/{number}/", "queue_id": None, "trigger_user_id": None,
            "trigger_source": "USER", "branch": "main", "commit_sha": "abc", "commit_message": "", "agent_name": "a",
            "image_ref": "", "started_at": at, "finished_at": at, "duration_seconds": 10, "result": result,
            "building": False, "raw_summary": "{}"}


class ParseCommandTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_command(cmd("/analyze"), "cicd_analysis_bot")["args"], [])
        self.assertEqual(parse_command(cmd("/analyze@cicd_analysis_bot activity-rpc 717"), "cicd_analysis_bot")["args"], ["activity-rpc", "717"])
        self.assertIsNone(parse_command(cmd("/analyze@ugopsbot"), "cicd_analysis_bot"))
        self.assertIsNone(parse_command(cmd("please /analyze"), "cicd_analysis_bot"))
        self.assertIsNone(parse_command(cmd("/analyzer"), "cicd_analysis_bot"))


class AnalyzeCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app = App(Config(database_path=os.path.join(self.tmp.name, "a.db"), database_url="", telegram_token="bot",
                              timezone="Asia/Kuala_Lumpur"))
        self.sent = []
        self.app.telegram.send = lambda chat, body, reply_to=None: self.sent.append((chat, body, reply_to)) or True
        self.app.telegram._username = "cicd_analysis_bot"
        self.analyzed = []
        self.app.commands.ensure_analyzed = self.fake_analyze
        self.now = datetime.now(timezone.utc)

    def add(self, job, number, minutes_ago, result="FAILURE", analyzed=True):
        job_id = self.app.db.upsert_job(infer_job(job, f"https://j/job/{job}/"))
        build_id, _ = self.app.db.save_build(job_id, build(number, (self.now - timedelta(minutes=minutes_ago)).isoformat(), result))
        if analyzed and result != "SUCCESS":
            self.app.db.save_failure(build_id, analyze(f"{job}.go:1:2: package x is not in std"))
        return build_id

    def fake_analyze(self, job, number):
        self.analyzed.append((job, number))
        build_id = self.app.db.query("SELECT b.id FROM builds b JOIN jobs j ON j.id=b.job_id WHERE j.job_name=? AND b.build_number=?", (job, number))[0]["id"]
        self.app.db.save_failure(build_id, analyze("error TS2304: Cannot find name 'x'"))

    def run_cmd(self, text, chat=TESTA):
        self.assertTrue(self.app.commands.handle(cmd(text, chat), background=False))

    def test_today_failures_of_this_group_most_recent_first(self):
        self.add("activity-rpc-testa", 717, 30)
        self.add("activity-rpc-testa", 716, 50)            # older failure of the same job: listed once
        self.add("testa-ug-app-ios", 111, 5, analyzed=False)  # most recent, not analyzed yet
        self.add("uat-activity-rpc-prod", 90, 1)            # another environment
        self.add("xgcash-next-testa", 20, 60 * 30)          # yesterday or earlier (30 h ago)
        self.add("wallet-rpc-testa", 7, 20)
        self.add("wallet-rpc-testa", 8, 10, result="SUCCESS")
        self.run_cmd("/analyze")
        self.assertEqual(self.analyzed, [("testa-ug-app-ios", 111)])
        progress, summary, full = [x[1] for x in self.sent]
        self.assertIn("正在分析其中 1 个", progress)
        self.assertNotIn("uat-activity-rpc-prod", summary)
        self.assertNotIn("xgcash-next-testa", summary)
        self.assertNotIn("#716", summary)
        order = [summary.index(x) for x in ("testa-ug-app-ios</b> #111", "wallet-rpc-testa</b> #7", "activity-rpc-testa</b> #717")]
        self.assertEqual(order, sorted(order))
        self.assertIn("已恢复（#8 成功）", summary)
        self.assertIn("testa-ug-app-ios</b> #111", full)  # full report of the most recent failure
        self.assertTrue(all(chat == TESTA and reply == 900 for chat, _, reply in self.sent))

    def test_nothing_failed_today(self):
        self.add("uat-activity-rpc-prod", 90, 1)
        self.run_cmd("/analyze")
        self.assertIn("今日 TESTA 暂无失败", self.sent[0][1])

    def test_one_job_by_service_name(self):
        self.add("activity-rpc-testa", 716, 50)
        self.add("activity-rpc-testa", 717, 30)
        self.run_cmd("/analyze@cicd_analysis_bot activity-rpc")
        self.assertIn("activity-rpc-testa</b> #717", self.sent[-1][1])
        self.run_cmd("/analyze activity-rpc-testa 716")
        self.assertIn("activity-rpc-testa</b> #716", self.sent[-1][1])
        self.assertEqual(self.analyzed, [])

    def test_other_chats_and_other_bots_are_ignored(self):
        self.add("activity-rpc-testa", 717, 30)
        self.assertTrue(self.app.commands.handle(cmd("/analyze", "-100123"), background=False))
        self.assertFalse(self.app.commands.handle(cmd("/analyze@ugopsbot"), background=False))
        self.assertEqual(self.sent, [])
