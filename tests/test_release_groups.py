"""Release-group messages as posted in the TESTA / UAT / PROD groups (2026-09-28)."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cicdanalysis.analyzer import analyze
from cicdanalysis.app import App
from cicdanalysis.collector import Collector
from cicdanalysis.config import Config
from cicdanalysis.jenkins import infer_job
from cicdanalysis.telegram import Telegram, parse_release_result, parse_trigger_message

TZ = "Asia/Kuala_Lumpur"
UAT = "-5592801576"
PROD = "-1003412281586"
CHATS = {"-1003919548725": "testa", UAT: "uat", PROD: "prod"}

UAT_TRIGGER = """🚀 Jenkins 发布触发通知

触发人: Infi (@infiwork6666)
触发项目: uat-activity-rpc-prod
触发时间: 2026-09-28 21:01:59
触发状态: ⏳ 已提交 Jenkins，等待流水线发布结果

服务类型: RPC 服务
服务名称: uat-activity-rpc
分支: uat
命名空间: uat-prod
"""

UAT_RESULT = """✅ Jenkins 发布通知
状态: 🟢 SUCCESS
发布服务名称: activity-rpc
分支名称: uat
发布通知: 发布完成 activity-rpc
详情: 服务=activity-rpc, 类型=rpc, Harbor项目=ugame, 分支=uat, 环境=uat-prod, 完成时间=2026-09-28 21:04:47 +0800
提交=b3ce6de35a5a
镜像=harbor.ugmid888.com/ugame/activity-rpc:activity-rpc-uat-20260928210209-f5c45283-b3ce6de35a5a
Jenkins=https://ugjekins.ugmid888.com/job/uat-activity-rpc-prod/228/

服务类型: rpc
Harbor项目: ugame
环境: uat-prod
任务: uat-activity-rpc-prod
构建号: 228
节点: ug-prod-master3
耗时: 2 min 38 sec and counting
提交信息: fix(UG-3285): isolate manual first-deposit grants on UAT
完成时间: 2026-09-28 21:04:47 +0800
"""

PROD_FAILED = UAT_RESULT.replace("✅ Jenkins 发布通知", "❌ Jenkins 发布通知").replace("🟢 SUCCESS", "🔴 FAILURE") \
    .replace("uat-activity-rpc-prod", "xgcash-admin-prod").replace("/228/", "/312/").replace("构建号: 228", "构建号: 312")

CONSOLE = """Started by user ugadmin
[Pipeline] stage
[Pipeline] { (Build)
+ pnpm install --frozen-lockfile
Lockfile is up to date, resolution step is skipped
+ pnpm build
src/pages/wallet.tsx(12,7): error TS2322: Type 'string' is not assignable to type 'number'.
 ELIFECYCLE  Command failed with exit code 2.
[Pipeline] }
ERROR: script returned exit code 2
Finished: FAILURE
"""


def update(text, chat, message_id=100, date=1790600000):
    return {"update_id": message_id, "message": {"message_id": message_id, "date": date, "chat": {"id": int(chat)}, "text": text}}


def build(number, started, result="SUCCESS", user_id=None):
    return {"build_number": number, "build_url": f"https://ugjekins.ugmid888.com/job/x/{number}/", "queue_id": None,
            "trigger_user_id": user_id, "trigger_source": "USER", "branch": "uat", "commit_sha": "b3ce6de35a5a",
            "commit_message": "", "agent_name": "a", "image_ref": "", "started_at": started, "finished_at": started,
            "duration_seconds": 150, "result": result, "building": False, "raw_summary": "{}"}


class FakeJenkins:
    def __init__(self, job, number, result, started_ms):
        self.job, self.number, self.result, self.started_ms = job, number, result, started_ms

    def jobs(self): return [{"name": self.job, "url": f"https://ugjekins.ugmid888.com/job/{self.job}/"}]
    def job_builds(self, url, limit): return [self.number]
    def build(self, url, number):
        return {"number": number, "url": f"{url}{number}/", "queueId": 9001, "timestamp": self.started_ms,
                "duration": 160000, "result": self.result, "building": False, "builtOn": "ug-prod-master1",
                "actions": [{"causes": [{"userId": "ugadmin", "userName": "ugadmin"}]}]}
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


class ParseTests(unittest.TestCase):
    def test_trigger_notice_without_build_url(self):
        event = parse_trigger_message(update(UAT_TRIGGER, UAT), TZ, CHATS)
        self.assertEqual(event["job_name"], "uat-activity-rpc-prod")
        self.assertEqual((event["trigger_name"], event["telegram_username"]), ("Infi", "infiwork6666"))
        self.assertEqual(event["environment"], "uat")
        self.assertEqual(event["branch"], "uat")
        self.assertEqual(event["build_url"], "")
        self.assertTrue(event["triggered_at"].startswith("2026-09-28T21:01:59"))

    def test_release_result(self):
        event = parse_release_result(update(UAT_RESULT, UAT, 101), TZ, CHATS)
        self.assertEqual((event["job_name"], event["build_number"], event["result"]), ("uat-activity-rpc-prod", 228, "SUCCESS"))
        self.assertEqual(event["environment"], "uat")
        self.assertEqual(event["telegram_message_id"], 101)
        self.assertEqual(event["finished_at"], "2026-09-28T21:04:47+08:00")
        self.assertEqual(parse_release_result(update(PROD_FAILED, PROD), TZ, CHATS)["result"], "FAILURE")
        # A trigger message is never mistaken for a result, and vice versa.
        self.assertIsNone(parse_release_result(update(UAT_TRIGGER, UAT), TZ, CHATS))
        self.assertIsNone(parse_trigger_message(update(UAT_RESULT, UAT), TZ, CHATS))

    def test_uat_job_with_prod_suffix_is_uat(self):
        self.assertEqual(infer_job("uat-activity-rpc-prod")["environment"], "uat")
        self.assertEqual(infer_job("uat-activity-rpc-prod")["service_name"], "activity-rpc")
        self.assertEqual(infer_job("xgcash-admin-prod")["environment"], "prod")
        self.assertEqual(infer_job("testa-ug-app")["environment"], "testa")


class LinkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(Config(database_path=os.path.join(self.tmp.name, "app.db"), database_url=""))

    def tearDown(self):
        self.tmp.cleanup()

    def job_id(self, name="uat-activity-rpc-prod"):
        return self.app.db.query("SELECT id FROM jobs WHERE job_name=?", (name,))[0]["id"]

    def trigger_user(self, number=228):
        return self.app.db.query("""SELECT u.display_name,u.telegram_username,b.trigger_source,j.environment FROM builds b
          JOIN jobs j ON j.id=b.job_id LEFT JOIN users u ON u.id=b.trigger_user_id WHERE b.build_number=?""", (number,))[0]

    def test_trigger_notice_then_build_matches_by_start_time(self):
        self.assertIsNotNone(self.app.handle_update(update(UAT_TRIGGER, UAT)))
        job_id = self.job_id()
        build_id, _ = self.app.db.save_build(job_id, build(228, "2026-09-28T13:02:09+00:00"))
        self.assertTrue(self.app.db.apply_trigger_event(job_id, build_id, 228, 9001, "2026-09-28T13:02:09+00:00"))
        row = self.trigger_user()
        self.assertEqual((row["display_name"], row["telegram_username"], row["trigger_source"]), ("Infi", "infiwork6666", "TELEGRAM_PANEL"))
        self.assertEqual(row["environment"], "uat")
        # A build long after the trigger is not attributed to it.
        other, _ = self.app.db.save_build(job_id, build(229, "2026-09-28T15:00:00+00:00"))
        self.assertFalse(self.app.db.apply_trigger_event(job_id, other, 229, None, "2026-09-28T15:00:00+00:00"))

    def test_linked_build_keeps_its_trigger_on_repoll(self):
        self.app.handle_update(update(UAT_TRIGGER, UAT, 100))
        job_id = self.job_id()
        build_id, _ = self.app.db.save_build(job_id, build(228, "2026-09-28T13:02:09+00:00"))
        self.app.db.apply_trigger_event(job_id, build_id, 228, None, "2026-09-28T13:02:09+00:00")
        # A second trigger 30s after the build started must not steal build 228 on the next poll.
        self.app.handle_update(update(UAT_TRIGGER.replace("Infi (@infiwork6666)", "PJ (@pjtsc)")
                                      .replace("21:01:59", "21:02:39"), UAT, 102))
        self.app.db.apply_trigger_event(job_id, build_id, 228, None, "2026-09-28T13:02:09+00:00")
        self.assertEqual(self.trigger_user()["telegram_username"], "infiwork6666")

    def test_result_message_assigns_build_number(self):
        self.app.handle_update(update(UAT_TRIGGER, UAT, 100))
        result = self.app.handle_update(update(UAT_RESULT, UAT, 101))
        self.assertTrue(result["trigger_linked"])
        self.assertEqual(self.app.work.get_nowait(), ("uat-activity-rpc-prod", 228))
        job_id = self.job_id()
        # Started time unknown to the matcher here: the build number alone links it.
        build_id, _ = self.app.db.save_build(job_id, build(228, None))
        self.assertTrue(self.app.db.apply_trigger_event(job_id, build_id, 228, None, None))
        self.assertEqual(self.trigger_user()["display_name"], "Infi")

    def test_trigger_after_build_collected(self):
        job_id = self.app.db.upsert_job(infer_job("uat-activity-rpc-prod"))
        self.app.db.save_build(job_id, build(228, "2026-09-28T13:02:09+00:00"))
        result = self.app.handle_update(update(UAT_TRIGGER, UAT))
        self.assertTrue(result["matched"])
        self.assertEqual(self.trigger_user()["display_name"], "Infi")

    def test_report_counts_trigger_user_in_uat(self):
        # The trigger time must be near "now": the report window is the last day.
        local = datetime.now(timezone.utc).astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S")
        self.app.handle_update(update(UAT_TRIGGER.replace("2026-09-28 21:01:59", local), UAT))
        job_id = self.job_id()
        now = datetime.now(timezone.utc).isoformat()
        build_id, _ = self.app.db.save_build(job_id, build(228, now))
        # triggered_at is fixed in the message; use the build number path to link.
        self.app.db.link_pending_trigger(job_id, 228, now)
        summary = self.app.reports.overview_days(1, "uat")
        self.assertEqual(summary["users"][0]["name"], "Infi")
        self.assertEqual(summary["panel_triggered"], 1)
        self.assertEqual(self.app.reports.overview_days(1, "prod")["total"], 0)


class FailureReportTests(unittest.TestCase):
    def test_failed_release_is_diagnosed_and_reported_once_as_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Config(database_path=os.path.join(folder, "app.db"), database_url="")
            app = App(config)
            trigger = UAT_TRIGGER.replace("Infi (@infiwork6666)", "PJ (@pjtsc)").replace("uat-activity-rpc-prod", "xgcash-admin-prod") \
                .replace("21:01:59", "15:06:12")
            app.handle_update(update(trigger, PROD, 200))
            app.handle_update(update(PROD_FAILED, PROD, 201))
            self.assertEqual(app.work.get_nowait(), ("xgcash-admin-prod", 312))
            telegram = FakeTelegram(app.db, config)
            started = int(datetime(2026, 9, 28, 7, 6, 19, tzinfo=timezone.utc).timestamp() * 1000)
            collector = Collector(app.db, FakeJenkins("xgcash-admin-prod", 312, "FAILURE", started), telegram)
            collector.collect_one("xgcash-admin-prod", 312)
            collector.collect_one("xgcash-admin-prod", 312)
            self.assertEqual(len(telegram.sent), 1)
            chat, text, reply_to = telegram.sent[0]
            self.assertEqual((chat, reply_to), (PROD, 201))
            self.assertIn("PJ (@pjtsc)", text)
            self.assertIn("xgcash-admin-prod</b> #312", text)
            self.assertIn("Build", text)
            self.assertIn("TS2322", text)
            self.assertIn("代码编译失败", text)


class AnalyzerTests(unittest.TestCase):
    def test_excerpt_surrounds_error_and_strips_pipeline_noise(self):
        result = analyze(CONSOLE, [{"name": "Build", "status": "FAILED"}])
        self.assertEqual(result["error_code"], "COMPILE_FAILED")
        self.assertIn("TS2322", result["error_excerpt"])
        self.assertNotIn("[Pipeline]", result["error_excerpt"])

    def test_unknown_failure_keeps_log_tail(self):
        result = analyze("step 1\nsomething odd happened\nFinished: FAILURE\n")
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertIn("something odd happened", result["error_excerpt"])

    def test_frozen_lockfile_flag_alone_is_not_a_lockfile_error(self):
        self.assertNotEqual(analyze("+ pnpm install --frozen-lockfile\nFinished: FAILURE")["error_code"], "ERR_PNPM_OUTDATED_LOCKFILE")
        self.assertEqual(analyze(" ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with \"frozen-lockfile\"")["error_code"], "ERR_PNPM_OUTDATED_LOCKFILE")

    def test_new_rules(self):
        self.assertEqual(analyze(" ELIFECYCLE  Command failed with exit code 1.")["error_code"], "BUILD_SCRIPT_FAILED")
        self.assertEqual(analyze("Tests run: 12, Failures: 2, Errors: 0")["error_code"], "TESTS_FAILED")
        self.assertEqual(analyze("FATAL ERROR: Reached heap limit - JavaScript heap out of memory")["error_code"], "OUT_OF_MEMORY")


if __name__ == "__main__":
    unittest.main()


class ReleaseMessageWebhookTests(unittest.TestCase):
    def test_forwarded_message_is_handled_like_group_message(self):
        with tempfile.TemporaryDirectory() as folder:
            app = App(Config(database_path=os.path.join(folder, "app.db"), database_url=""))
            result = app.record_release_message({"chat_id": UAT, "message_id": 7, "text": UAT_TRIGGER})
            self.assertTrue(result["handled"])
            self.assertEqual(result["result"]["telegram_username"], "infiwork6666")
            ignored = app.record_release_message({"chat_id": "-42", "message_id": 8, "text": UAT_TRIGGER})
            self.assertFalse(ignored["handled"])
            with self.assertRaises(ValueError):
                app.record_release_message({"chat_id": UAT, "text": UAT_TRIGGER})
