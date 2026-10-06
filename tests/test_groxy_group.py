"""UG-groxy环境发布群 (-5418156281), release bot @uggroxycicdbot."""
import unittest

from cicdanalysis.config import Config
from cicdanalysis.jenkins import infer_job
from cicdanalysis.telegram import parse_release_result, parse_trigger_message

TZ = "Asia/Kuala_Lumpur"
GROXY = "-5418156281"

TRIGGER = """🚀 Jenkins 发布触发通知

触发人: Infi (@infiwork6666)
触发项目: activity-rpc-groxy
触发时间: 2026-10-06 18:01:59
触发状态: ⏳ 已提交 Jenkins，等待流水线发布结果
"""

RESULT = """❌ Jenkins 发布通知
状态: 🔴 FAILURE
任务: activity-rpc-groxy
构建号: 12
Jenkins: https://ugjekins.ugmid888.com/job/activity-rpc-groxy/12/
完成时间: 2026-10-06 18:04:47 +0800
"""


def update(text, chat, message_id=100):
    return {"update_id": message_id, "message": {"message_id": message_id, "date": 1791280000, "chat": {"id": int(chat)}, "text": text}}


class GroxyGroupTests(unittest.TestCase):
    def test_group_is_a_release_group(self):
        config = Config()
        self.assertEqual(config.groxy_chat_id, GROXY)
        self.assertEqual(config.chat_for("groxy"), GROXY)
        self.assertEqual(config.chat_environments()[GROXY], "groxy")
        self.assertIn(GROXY, config.telegram_allowed_chats())

    def test_messages_in_the_group_belong_to_groxy(self):
        chats = Config().chat_environments()
        trigger = parse_trigger_message(update(TRIGGER, GROXY), TZ, chats)
        self.assertEqual((trigger["job_name"], trigger["environment"]), ("activity-rpc-groxy", "groxy"))
        result = parse_release_result(update(RESULT, GROXY, 101), TZ, chats)
        self.assertEqual((result["build_number"], result["result"], result["environment"]), (12, "FAILURE", "groxy"))

    def test_groxy_job_name(self):
        self.assertEqual(infer_job("activity-rpc-groxy")["environment"], "groxy")
        self.assertEqual(infer_job("activity-rpc-groxy")["service_name"], "activity-rpc")


if __name__ == "__main__":
    unittest.main()
