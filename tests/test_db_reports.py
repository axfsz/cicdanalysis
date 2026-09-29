import os,tempfile,unittest
from datetime import datetime,timedelta,timezone
from cicdanalysis.db import Database
from cicdanalysis.jenkins import infer_job
from cicdanalysis.reports import Reports


class DBReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(os.path.join(self.tmp.name,"test.db"));self.db.init()
    def tearDown(self):self.tmp.cleanup()
    def test_trigger_user_report(self):
        job=self.db.upsert_job(infer_job("xgcash-admin-prod","https://j/job/xgcash-admin-prod/"));user=self.db.upsert_user("mew","mew","slo_dream_03")
        started=datetime.now(timezone.utc)-timedelta(hours=1)
        base={"build_url":"https://j/1/","queue_id":1,"trigger_user_id":user,"trigger_source":"USER","branch":"main","commit_sha":"abc","commit_message":"fix","agent_name":"a","image_ref":"","started_at":started.isoformat(),"finished_at":datetime.now(timezone.utc).isoformat(),"duration_seconds":60,"building":False,"raw_summary":"{}"}
        for n,result in ((1,"SUCCESS"),(2,"FAILURE")):
            self.db.save_build(job,{**base,"build_number":n,"result":result})
        s=Reports(self.db,"Asia/Kuala_Lumpur").overview_days(1,"prod")
        self.assertEqual(s["total"],2);self.assertEqual(s["users"][0]["name"],"mew");self.assertEqual(s["success_rate"],50.0)

    def test_selected_date_uses_kuala_lumpur_day_boundaries(self):
        reports=Reports(self.db,"Asia/Kuala_Lumpur")
        start,end=reports.date_bounds("2026-09-07")
        self.assertEqual(start.isoformat(),"2026-09-06T16:00:00+00:00")
        self.assertEqual(end.isoformat(),"2026-09-07T16:00:00+00:00")
        self.assertIn("2026-09-07",reports.text_for_date("2026-09-07","testa"))

    def test_invalid_selected_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"YYYY-MM-DD"):
            Reports(self.db,"Asia/Kuala_Lumpur").date_bounds("07-09-2026")

    def test_missing_identity_is_reported_as_not_connected(self):
        job=self.db.upsert_job(infer_job("uat-ug-app","https://j/job/uat-ug-app/"))
        started=datetime.now(timezone.utc)-timedelta(hours=1)
        self.db.save_build(job,{"build_number":1,"build_url":"https://j/job/uat-ug-app/1/","queue_id":1,
          "trigger_user_id":None,"trigger_source":"UNKNOWN","branch":"main","commit_sha":"abc","commit_message":"",
          "agent_name":"a","image_ref":"","started_at":started.isoformat(),"finished_at":started.isoformat(),
          "duration_seconds":60,"result":"SUCCESS","building":False,"raw_summary":"{}"})
        summary=Reports(self.db,"Asia/Kuala_Lumpur").overview_days(1,"uat")
        self.assertEqual(summary["trigger_identity_status"],"NOT_CONNECTED")


if __name__=="__main__":unittest.main()
