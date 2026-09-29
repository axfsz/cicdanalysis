import unittest
from cicdanalysis.jenkins import infer_job,parse_build,parse_console_trigger


class JenkinsTests(unittest.TestCase):
    def test_infer_job(self):
        m=infer_job("xgcash-admin-testa","https://jenkins/job/xgcash-admin-testa/")
        self.assertEqual(m["project"],"xgcash");self.assertEqual(m["environment"],"testa");self.assertEqual(m["service_type"],"web")

    def test_parse_user_and_commit(self):
        b=parse_build({"number":1922,"url":"https://j/1922/","timestamp":1788762704000,"duration":142000,"result":"SUCCESS","building":False,"builtOn":"eks-agent1",
          "actions":[{"causes":[{"userId":"mew","userName":"mew (@slo_dream_03)"}]},{"parameters":[{"name":"BRANCH_NAME","value":"main"}]}],
          "changeSet":{"items":[{"commitId":"e65242a7efae","msg":"fix issue","author":{"fullName":"mew"}}]}})
        self.assertEqual(b["trigger_username"],"mew");self.assertEqual(b["telegram_username"],"slo_dream_03");self.assertEqual(b["commit_sha"],"e65242a7efae")

    def test_parse_telegram_build_parameters(self):
        b=parse_build({"number":101,"actions":[{"parameters":[
          {"name":"TRIGGER_USER_NAME","value":"Mars Stephen"},{"name":"TELEGRAM_USERNAME","value":"@marsstephen"},
          {"name":"TELEGRAM_USER_ID","value":"860100001"}]}]})
        self.assertEqual(b["trigger_display"],"Mars Stephen")
        self.assertEqual(b["telegram_username"],"marsstephen")
        self.assertEqual(b["telegram_user_id"],"860100001")
        self.assertEqual(b["trigger_source"],"TELEGRAM_PANEL")

    def test_parse_console_trigger(self):
        found=parse_console_trigger("触发人: Mars Stephen (@marsstephen)\n触发项目: statistics-testa")
        self.assertEqual(found["trigger_display"],"Mars Stephen")
        self.assertEqual(found["telegram_username"],"marsstephen")


if __name__=="__main__":unittest.main()
