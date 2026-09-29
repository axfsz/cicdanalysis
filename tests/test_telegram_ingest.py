import unittest
import os
import tempfile

from cicdanalysis.app import App
from cicdanalysis.config import Config
from cicdanalysis.telegram import parse_trigger_message


MESSAGE="""📣 Jenkins 发布触发结果

触发人: Mars Stephen (@marsstephen)
触发项目: statistics-testa
Git分支: main
触发时间: 2026-09-07 15:57:17
触发状态: ✅ SUCCESS，已成功加入 Jenkins 构建队列

服务类型: 独立服务
服务名称: statistics
命名空间: testa
HTTP状态: 201
队列地址: https://ugjekins.ugmid888.com/queue/item/32727/
构建地址: https://ugjekins.ugmid888.com/job/statistics-testa/101/
"""


class TelegramIngestTests(unittest.TestCase):
    def test_parse_group_trigger_receipt(self):
        update={"update_id":77,"message":{"message_id":45,"date":1788770000,
                "chat":{"id":-1003919548725},"text":MESSAGE}}
        event=parse_trigger_message(update,"Asia/Kuala_Lumpur",{"-1003919548725"})
        self.assertEqual(event["event_id"],"telegram-message:-1003919548725:45")
        self.assertEqual(event["job_name"],"statistics-testa")
        self.assertEqual(event["build_url"],"https://ugjekins.ugmid888.com/job/statistics-testa/101/")
        self.assertEqual(event["trigger_name"],"Mars Stephen")
        self.assertEqual(event["telegram_username"],"marsstephen")
        self.assertEqual(event["branch"],"main")
        self.assertEqual(event["environment"],"testa")
        self.assertEqual(event["http_status"],201)
        self.assertTrue(event["triggered_at"].endswith("+08:00"))

    def test_reject_other_group_and_unrelated_message(self):
        update={"update_id":78,"message":{"message_id":46,"chat":{"id":-1},"text":MESSAGE}}
        self.assertIsNone(parse_trigger_message(update,"Asia/Kuala_Lumpur",{"-2"}))
        update["message"]["chat"]["id"]=-2;update["message"]["text"]="hello"
        self.assertIsNone(parse_trigger_message(update,"Asia/Kuala_Lumpur",{"-2"}))

    def test_group_message_repairs_existing_build(self):
        with tempfile.TemporaryDirectory() as folder:
            app=App(Config(database_path=os.path.join(folder,"app.db")))
            job_id=app.db.upsert_job({"job_name":"statistics-testa","job_url":"https://ugjekins.ugmid888.com/job/statistics-testa/",
              "project":"statistics","service_name":"statistics","service_type":"standalone","environment":"testa","namespace":"testa"})
            app.db.save_build(job_id,{"build_number":101,"build_url":"https://ugjekins.ugmid888.com/job/statistics-testa/101/",
              "queue_id":32727,"trigger_user_id":None,"trigger_source":"UNKNOWN","branch":"main","commit_sha":"abc",
              "commit_message":"","agent_name":"a","image_ref":"","started_at":"2026-09-07T07:57:17+00:00",
              "finished_at":"2026-09-07T07:58:18+00:00","duration_seconds":61,"result":"SUCCESS","building":False,"raw_summary":"{}"})
            update={"update_id":77,"message":{"message_id":45,"date":1788770000,
                    "chat":{"id":-1003919548725},"text":MESSAGE}}
            result=app.record_trigger(parse_trigger_message(update,"Asia/Kuala_Lumpur",{"-1003919548725"}))
            self.assertTrue(result["matched"])
            row=app.db.query("""SELECT u.display_name,u.telegram_username,b.trigger_source FROM builds b
              JOIN users u ON u.id=b.trigger_user_id WHERE b.build_number=101""")[0]
            self.assertEqual(row["display_name"],"Mars Stephen")
            self.assertEqual(row["telegram_username"],"marsstephen")
            self.assertEqual(row["trigger_source"],"TELEGRAM_PANEL")


if __name__=="__main__": unittest.main()
