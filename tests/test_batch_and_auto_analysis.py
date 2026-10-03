"""0.8.0: batch trigger notices and automatic failure analysis (2026-09-30 group messages)."""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from cicdanalysis.app import App
from cicdanalysis.collector import Collector
from cicdanalysis.config import Config
from cicdanalysis.jenkins import cause_source, parse_build
from cicdanalysis.telegram import Telegram, parse_release_result, parse_trigger_messages

TZ = "Asia/Kuala_Lumpur"
TESTA, UAT = "-1003919548725", "-5592801576"
CHATS = {TESTA: "testa", UAT: "uat", "-1003412281586": "prod"}

BATCH = """🚀 Jenkins 批量发布触发通知

触发人: whisper (@whisper891)
环境: uat-prod
分支: uat
服务数量: 3
触发时间: {when}

• uat-report-rpc-prod
• uat-bff-merchant-prod
• uat-xgcash-admin-prod

触发状态: ⏳ 已开始提交 Jenkins，最终结果以 Jenkinsfile webhook 通知为准。

📌 构建面板已回到底部

UG UAT-Prod Jenkins 预发构建面板

默认分支: uat
默认命名空间: uat-prod
Jenkins: https://ugjekins.ugmid888.com
Job 数量: 28
"""

TESTA_FAILED = """❌ Jenkins 发布通知
状态: 🔴 FAILURE
发布服务名称: game-rpc
分支名称: main
发布通知: 发布失败 game-rpc
详情: 服务=game-rpc, 类型=rpc, Harbor项目=ugame, 分支=main, 环境=test, 完成时间=2026-09-30 16:18:22 +0800
提交=98a4ca2af796
Jenkins=https://ugjekins.ugmid888.com/job/game-rpc-testa/341/

服务类型: rpc
Harbor项目: ugame
环境: test
任务: game-rpc-testa
构建号: 341
节点: eks-agent2
耗时: 49 sec and counting
提交信息: Merge branch 'fix/UG-301' into main
完成时间: 2026-09-30 16:18:22 +0800
"""

CONSOLE = """Started by user ugadmin
[Pipeline] { (Build)
+ go build ./...
services/game/rpc/main.go:13:2: package UltraGaming/services/game/provider/accountmeta is not in std
ERROR: script returned exit code 1
Finished: FAILURE
"""


def update(text, chat, message_id=500, date=None):
    return {"update_id": message_id, "message": {"message_id": message_id, "date": date or int(time.time()),
                                                  "chat": {"id": int(chat)}, "text": text}}


def local_now(delta_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")


class StagedJenkins:
    """A build that is still running on the first read(s) and final afterwards."""
    def __init__(self, job, number, running_reads=1, result="FAILURE", started=None):
        self.job, self.number, self.running_reads, self.result = job, number, running_reads, result
        self.started_ms = int((started or datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp() * 1000)
        self.reads = 0

    def jobs(self): return [{"name": self.job, "url": f"https://ugjekins.ugmid888.com/job/{self.job}/"}]
    def job_builds(self, url, limit): return [self.number]
    def build(self, url, number):
        self.reads += 1
        running = self.reads <= self.running_reads
        return {"number": number, "url": f"{url}{number}/", "queueId": 38117, "timestamp": self.started_ms,
                "duration": 0 if running else 49000, "result": None if running else self.result, "building": running,
                "builtOn": "eks-agent2", "actions": [{"causes": [{"userId": "ugadmin", "userName": "ugadmin"}]}]}
    def console_head(self, url, number, size): return CONSOLE[:size]
    def console(self, url, number, size): return CONSOLE
    def stages(self, url, number): return [{"name": "Build", "status": "FAILED"}]
    def failed_step_logs(self, url, number, stages): return ""
    def job_url_for(self, name): return f"https://ugjekins.ugmid888.com/job/{name}/"


class FakeTelegram(Telegram):
    def __init__(self, db, config):
        super().__init__("token", db, config.chat_for); self.sent = []
    def send(self, chat_id, text, reply_to=None):
        self.sent.append((chat_id, text, reply_to)); return True


class BatchParseTests(unittest.TestCase):
    def test_batch_notice_yields_one_event_per_service(self):
        events = parse_trigger_messages(update(BATCH.format(when="2026-09-30 16:36:42"), UAT), TZ, CHATS)
        self.assertEqual([e["job_name"] for e in events],
                         ["uat-report-rpc-prod", "uat-bff-merchant-prod", "uat-xgcash-admin-prod"])
        for e in events:
            self.assertEqual((e["trigger_name"], e["telegram_username"]), ("whisper", "whisper891"))
            self.assertEqual((e["environment"], e["branch"], e["trigger_mode"], e["batch_size"]), ("uat", "uat", "BATCH", 3))
            self.assertTrue(e["triggered_at"].startswith("2026-09-30T16:36:42+08:00"))
        self.assertEqual(len({e["event_id"] for e in events}), 3)
        self.assertEqual(len({e["batch_id"] for e in events}), 1)

    def test_panel_text_after_the_list_is_ignored(self):
        events = parse_trigger_messages(update(BATCH.format(when="2026-09-30 16:36:42"), UAT), TZ, CHATS)
        self.assertNotIn("ugjekins.ugmid888.com", " ".join(e["job_name"] for e in events))

    def test_batch_result_with_build_urls_links_numbers(self):
        text = """📣 Jenkins 批量发布触发结果
触发人: PJ (@pjtsc)
触发时间: 2026-09-30 16:40:00
• game-rpc-testa: https://ugjekins.ugmid888.com/job/game-rpc-testa/342/
• bff-player-testa https://ugjekins.ugmid888.com/queue/item/38120/
"""
        events = parse_trigger_messages(update(text, TESTA), TZ, CHATS)
        self.assertEqual([(e["job_name"], e["build_url"], e["queue_url"]) for e in events], [
            ("game-rpc-testa", "https://ugjekins.ugmid888.com/job/game-rpc-testa/342/", ""),
            ("bff-player-testa", "", "https://ugjekins.ugmid888.com/queue/item/38120/")])

    def test_batch_notice_is_not_a_release_result(self):
        self.assertIsNone(parse_release_result(update(BATCH.format(when="2026-09-30 16:36:42"), UAT), TZ, CHATS))

    def test_single_notice_unchanged(self):
        text = "🚀 Jenkins 发布触发通知\n\n触发人: Infi (@infiwork6666)\n触发项目: uat-activity-rpc-prod\n触发时间: 2026-09-28 21:01:59\n"
        events = parse_trigger_messages(update(text, UAT), TZ, CHATS)
        self.assertEqual([(e["job_name"], e["trigger_mode"]) for e in events], [("uat-activity-rpc-prod", "SINGLE")])


class BatchLinkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(Config(database_path=os.path.join(self.tmp.name, "app.db"), database_url=""))

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_batch_build_gets_the_real_trigger_user(self):
        result = self.app.handle_update(update(BATCH.format(when=local_now(-60)), UAT))
        self.assertTrue(result["batch"]); self.assertEqual(result["count"], 3)
        started = datetime.now(timezone.utc).isoformat()
        for number, job in enumerate(["uat-report-rpc-prod", "uat-bff-merchant-prod", "uat-xgcash-admin-prod"], 140):
            job_id = self.app.db.query("SELECT id FROM jobs WHERE job_name=?", (job,))[0]["id"]
            build_id, _ = self.app.db.save_build(job_id, {
                "build_number": number, "build_url": "", "queue_id": None, "trigger_user_id": None,
                "trigger_source": "REMOTE", "branch": "uat", "commit_sha": "b5e07c1301c8", "commit_message": "",
                "agent_name": "", "image_ref": "", "started_at": started, "finished_at": started,
                "duration_seconds": 120, "result": "SUCCESS", "building": False, "raw_summary": "{}"})
            self.assertTrue(self.app.db.apply_trigger_event(job_id, build_id, number, None, started))
        summary = self.app.reports.overview_days(1, "uat")
        self.assertEqual(summary["users"][0]["name"], "whisper")
        self.assertEqual((summary["users"][0]["total"], summary["users"][0]["batch"]), (3, 3))
        self.assertEqual((summary["batch_builds"], summary["batch_messages"], summary["unattributed"]), (3, 1, 0))
        self.assertEqual(summary["trigger_identity_status"], "OK")

    def test_unattributed_release_is_not_called_automatic(self):
        job_id = self.app.db.upsert_job({"project": "ugame", "job_name": "uat-report-rpc-prod", "service_name": "report-rpc",
                                         "service_type": "rpc", "environment": "uat", "namespace": "uat-prod"})
        now = datetime.now(timezone.utc).isoformat()
        for number, source in ((1, "REMOTE"), (2, "TIMER")):
            self.app.db.save_build(job_id, {"build_number": number, "trigger_source": source, "trigger_user_id": None,
                                            "started_at": now, "finished_at": now, "duration_seconds": 1,
                                            "result": "SUCCESS", "building": False})
        names = {u["name"]: u["kind"] for u in self.app.reports.overview_days(1, "uat")["users"]}
        self.assertEqual(names, {"未识别触发人": "unknown", "自动触发": "auto"})
        summary = self.app.reports.overview_days(1, "uat")
        self.assertEqual((summary["automatic"], summary["unattributed"]), (1, 1))


class AutoAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(database_path=os.path.join(self.tmp.name, "app.db"), database_url="")
        self.app = App(self.config)
        self.telegram = FakeTelegram(self.app.db, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def collector(self, jenkins):
        return Collector(self.app.db, jenkins, self.telegram)

    def test_failure_announced_while_running_is_reported_when_final(self):
        """Screenshot 2: "❌ 发布通知" with "49 sec and counting" — Jenkins still says building."""
        self.app.handle_update(update(TESTA_FAILED, TESTA, 900))
        self.assertEqual(self.app.work.get_nowait(), ("game-rpc-testa", 341))
        collector = self.collector(StagedJenkins("game-rpc-testa", 341, running_reads=1))
        collector.collect_one("game-rpc-testa", 341)   # still running: nothing to analyze yet
        self.assertEqual(self.telegram.sent, [])
        collector.collect_one("game-rpc-testa", 341)   # follow-up read: final FAILURE
        self.assertEqual(len(self.telegram.sent), 1)
        chat, text, reply_to = self.telegram.sent[0]
        self.assertEqual((chat, reply_to), (TESTA, 900))
        self.assertIn("game-rpc-testa</b> #341", text)
        collector.collect_one("game-rpc-testa", 341)
        self.assertEqual(len(self.telegram.sent), 1)   # once per build

    def test_poller_reports_build_first_seen_running(self):
        """No group message at all: polling alone must report it (was blocked by last_build_number)."""
        jenkins = StagedJenkins("game-rpc-testa", 341, running_reads=1)
        collector = self.collector(jenkins)
        collector.collect_all()
        collector.collect_all()
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.telegram.sent[0][0], TESTA)

    def test_old_history_is_not_flooded(self):
        old = datetime.now(timezone.utc) - timedelta(days=2)
        self.collector(StagedJenkins("game-rpc-testa", 12, running_reads=0, started=old)).collect_all()
        self.assertEqual(self.telegram.sent, [])

    def test_aborted_is_not_reported_by_default(self):
        self.collector(StagedJenkins("game-rpc-testa", 13, running_reads=0, result="ABORTED")).collect_all()
        self.assertEqual(self.telegram.sent, [])

    def test_follow_up_requeues_running_builds(self):
        self.app.config.__class__  # frozen dataclass; defaults are used
        job_id = self.app.db.upsert_job({"project": "ugame", "job_name": "game-rpc-testa", "service_name": "game-rpc",
                                         "service_type": "rpc", "environment": "testa", "namespace": "testa"})
        self.app.db.save_build(job_id, {"build_number": 341, "result": "RUNNING", "building": True,
                                        "started_at": datetime.now(timezone.utc).isoformat()})
        self.app._follow_up("game-rpc-testa", 341)
        self.assertEqual(self.app.follow_ups[("game-rpc-testa", 341)], 1)
        self.app.db.save_build(job_id, {"build_number": 341, "result": "FAILURE", "building": False,
                                        "started_at": datetime.now(timezone.utc).isoformat()})
        self.app._follow_up("game-rpc-testa", 341)
        self.assertNotIn(("game-rpc-testa", 341), self.app.follow_ups)

    def test_dashboard_analysis_counts_auto_reports(self):
        self.collector(StagedJenkins("game-rpc-testa", 341, running_reads=0)).collect_all()
        a = self.app.reports.overview_days(1, "testa")["analysis"]
        self.assertEqual((a["failed"], a["analyzed"], a["auto_reported"], a["unreported"]), (1, 1, 1, 0))
        self.assertEqual(a["recent"][0]["report_source"], "AUTO")
        self.assertIsNotNone(a["recent"][0]["latency_seconds"])


class CauseTests(unittest.TestCase):
    def test_remote_api_trigger_is_not_automatic(self):
        self.assertEqual(cause_source([{"_class": "hudson.model.Cause$RemoteCause", "shortDescription": "Started by remote host 10.0.0.1"}]), "REMOTE")
        self.assertEqual(cause_source([{"shortDescription": "Started by timer"}]), "TIMER")
        self.assertEqual(cause_source([{"shortDescription": "Started by an SCM change"}]), "SCM")
        self.assertEqual(parse_build({"number": 1, "actions": [{"causes": [{"shortDescription": "Started by remote host x"}]}]})["trigger_source"], "REMOTE")


if __name__ == "__main__":
    unittest.main()
