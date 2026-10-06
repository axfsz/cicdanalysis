import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cicdanalysis.analyzer import analyze
from cicdanalysis.app import App
from cicdanalysis.collector import Collector
from cicdanalysis.config import Config
from cicdanalysis.jenkins import JenkinsClient, failed_downstream, strip_html
from cicdanalysis.llm import LLMAnalyzer

CONSOLE = """Started by user ugadmin
[Pipeline] stage
+ echo deploying with token=abc123secret
+ git clone https://deploy:hunter2@git.example.com/app.git
[Pipeline] parallel
[Branch: lint] warning: Could not resolve host in optional mirror
[Branch: build] + go build ./...
[Branch: build] ./internal/wallet/grant.go:88:14: undefined: GrantV2
ERROR: script returned exit code 1
Finished: FAILURE
"""
FOCUS = """===== stage: Build / step: Shell Script go build ./...
+ go build ./...
./internal/wallet/grant.go:88:14: undefined: GrantV2
ERROR: script returned exit code 1"""

MODEL_ANSWER = {"error_category": "CODE_ERROR",
                "root_cause": "internal/wallet/grant.go 第 88 行引用了不存在的 GrantV2，Go 编译失败。",
                "suggestion": "确认 GrantV2 是否已提交；本地执行 go build ./... 复现后修复",
                "responsibility_type": "CODE",
                "key_lines": ["./internal/wallet/grant.go:88:14: undefined: GrantV2", "invented line not in log"],
                "confidence": 0.92}


class FakeModel(BaseHTTPRequestHandler):
    requests, status = [], 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.requests.append((self.headers.get("Authorization"), body))
        if FakeModel.status != 200:
            self.send_response(FakeModel.status); self.end_headers(); self.wfile.write(b"quota"); return
        payload = {"choices": [{"message": {"content": "```json\n" + json.dumps(MODEL_ANSWER, ensure_ascii=False) + "\n```"}}]}
        data = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args): pass


class FakeJenkins:
    def __init__(self): self.console_calls = []
    def jobs(self): return [{"name": "wallet-rpc-prod", "url": "https://j/job/wallet-rpc-prod/"}]
    def job_builds(self, url, limit): return [7]
    def build(self, url, number):
        return {"number": number, "url": f"{url}{number}/", "queueId": 1, "timestamp": 1790600000000, "duration": 1000,
                "result": "FAILURE", "building": False, "actions": []}
    def console_head(self, url, number, size): return ""
    def console(self, url, number, size): self.console_calls.append((url, number)); return CONSOLE
    def stages(self, url, number): return [{"name": "Build", "status": "FAILED", "id": "12"}]
    def failed_step_logs(self, url, number, stages): return FOCUS
    def job_url_for(self, name): return f"https://j/job/{name}/"


class LLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeModel.requests.clear(); FakeModel.status = 200
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(Config(database_path=os.path.join(self.tmp.name, "a.db"), database_url=""))
        self.llm = LLMAnalyzer(self.base, "sk-test", "deepseek-chat")
        self.jenkins = FakeJenkins()
        self.collector = Collector(self.app.db, self.jenkins, None, llm=self.llm)

    def tearDown(self):
        self.tmp.cleanup()

    def stored(self):
        return self.app.db.query("SELECT * FROM build_failures ORDER BY id")

    def test_model_diagnosis_is_stored_once_and_secrets_never_leave(self):
        self.collector.collect_one("wallet-rpc-prod", 7)
        self.collector.collect_all()  # re-poll: final result unchanged, no second download or model call
        self.assertEqual(len(FakeModel.requests), 1)
        self.assertEqual(len(self.jenkins.console_calls), 1)
        auth, body = FakeModel.requests[0]
        self.assertEqual(auth, "Bearer sk-test")
        self.assertEqual(body["model"], "deepseek-chat")
        prompt = body["messages"][1]["content"]
        self.assertIn("undefined: GrantV2", prompt)
        self.assertNotIn("abc123secret", prompt)
        self.assertNotIn("hunter2", prompt)
        row = self.stored()[0]
        self.assertEqual((row["analysis_source"], row["analysis_model"]), ("LLM", "deepseek-chat"))
        self.assertIn("GrantV2", row["root_cause"])
        self.assertEqual(row["error_category"], "CODE_ERROR")
        # Invented key lines are dropped; real ones kept.
        self.assertEqual(row["error_excerpt"], "./internal/wallet/grant.go:88:14: undefined: GrantV2")

    def test_same_error_on_another_build_reuses_the_diagnosis(self):
        self.collector.collect_one("wallet-rpc-prod", 7)
        self.jenkins.job_builds = lambda url, limit: [8]
        self.collector.collect_one("wallet-rpc-prod", 8)
        self.assertEqual(len(FakeModel.requests), 1)
        self.assertEqual([r["analysis_source"] for r in self.stored()], ["LLM", "LLM"])

    def test_model_failure_falls_back_to_rules(self):
        FakeModel.status = 429
        self.collector.collect_one("wallet-rpc-prod", 7)
        row = self.stored()[0]
        self.assertEqual(row["analysis_source"], "RULE")
        self.assertEqual(row["error_code"], "COMPILE_FAILED")

    def test_without_api_key_no_call_is_made(self):
        Collector(self.app.db, self.jenkins, None, llm=LLMAnalyzer(self.base, "", "deepseek-chat")).collect_one("wallet-rpc-prod", 7)
        self.assertEqual(FakeModel.requests, [])
        self.assertEqual(self.stored()[0]["analysis_source"], "RULE")


class FocusTests(unittest.TestCase):
    def test_failed_step_log_wins_over_noise_elsewhere(self):
        result = analyze(CONSOLE, [{"name": "Build", "status": "FAILED"}], "FAILURE", FOCUS)
        self.assertEqual(result["error_code"], "COMPILE_FAILED")
        self.assertIn("GrantV2", result["error_excerpt"])
        # Without the focus log, the optional-mirror warning is matched first (the old behaviour).
        self.assertEqual(analyze(CONSOLE)["error_code"], "NETWORK_FAILURE")

    def test_unknown_failure_points_at_first_error(self):
        result = analyze("a\nstep x failed with code 3\nlater ERROR: cleanup failed\nFinished: FAILURE")
        self.assertEqual(result["normalized_error"], "step x failed with code 3")
        self.assertIn("step x failed", result["error_excerpt"])

    def test_downstream_and_wfapi_helpers(self):
        console = ("Starting building: deploy-helm #41\n"
                   "Build deploy » helm-prod #41 completed: FAILURE\n"
                   "deploy-k8s #9 completed with status FAILURE (propagate: false to ignore)\n")
        self.assertEqual(failed_downstream(console), [("deploy » helm-prod", 41), ("deploy-k8s", 9)])
        client = JenkinsClient("https://j", "u", "t")
        self.assertEqual(client.job_url_for("deploy » helm-prod"), "https://j/job/deploy/job/helm-prod/")
        self.assertEqual(strip_html('<span class="x">a &amp; b</span>'), "a & b")
        calls = {"https://j/job/x/5/execution/node/12/wfapi/describe":
                     {"stageFlowNodes": [{"id": "13", "status": "SUCCESS"},
                                         {"id": "14", "status": "FAILED", "name": "Shell Script",
                                          "parameterDescription": "make build", "error": {"message": "script returned exit code 2"}}]},
                 "https://j/job/x/5/execution/node/14/wfapi/log": {"text": "<b>make: *** [build] Error 2</b>"}}
        client._get = lambda url, raw=False: calls[url]
        text = client.failed_step_logs("https://j/job/x/", 5, [{"name": "Build", "status": "FAILED", "id": "12"},
                                                                {"name": "Test", "status": "SUCCESS", "id": "20"}])
        self.assertIn("step: Shell Script make build", text)
        self.assertIn("make: *** [build] Error 2", text)
        self.assertIn("ERROR: script returned exit code 2", text)


if __name__ == "__main__":
    unittest.main()


IOS_FAILED = """❌ Jenkins 发布通知
状态: 🔴 FAILURE
发布服务名称: testa-ug-app-ios
分支名称: -
发布通知: -
详情: -
提交=-
镜像=-
Jenkins=https://ugjekins.ugmid888.com/view/testa/job/testa-ug-app-ios/111/

服务类型: -
Harbor项目: -
环境: testa
任务: -
构建号: 111
节点: -
耗时: -
提交信息: -
完成时间: 2026-09-28 10:18:37 UTC
"""

IOS_CONSOLE = """Started by user ugadmin
[Pipeline] stage (Archive)
+ xcodebuild -workspace UG.xcworkspace -scheme UG archive
error: No profiles for 'com.ug.app' were found: Xcode couldn't find any iOS App Development provisioning profiles matching 'com.ug.app'.
** ARCHIVE FAILED **
ERROR: script returned exit code 65
Finished: FAILURE
"""


class GroupFailureEndToEndTests(unittest.TestCase):
    """❌ 发布通知 in the TESTA group -> Jenkins log of that job/build -> model -> report in the same group."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_flow(self, text, jobs):
        FakeModel.requests.clear(); FakeModel.status = 200
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        config = Config(database_path=os.path.join(tmp.name, "a.db"), database_url="", jenkins_url="https://ugjekins.ugmid888.com",
                        jenkins_token="t", telegram_token="bot", llm_api_key="sk", llm_base_url=f"http://127.0.0.1:{self.server.server_port}/v1")
        app = self.app = App(config)
        jenkins = FakeJenkins()
        jenkins.jobs = lambda: [{"name": n, "url": f"https://ugjekins.ugmid888.com/job/{n}/"} for n in jobs]
        jenkins.console = lambda url, number, size: jenkins.console_calls.append((url, number)) or IOS_CONSOLE
        jenkins.failed_step_logs = lambda url, number, stages: ""
        app.jenkins = app.collector.client = jenkins
        sent = []
        app.telegram.send = lambda chat, body, reply_to=None: sent.append((chat, body, reply_to)) or True
        app.collector.notifier = app.telegram
        update = {"update_id": 1, "message": {"message_id": 555, "date": 1790590000, "chat": {"id": -1003919548725}, "text": text}}
        result = app.handle_update(update)
        name, number = app.work.get_nowait()
        app.collector.collect_one(name, number)
        return result, jenkins, sent

    def test_ios_job_from_view_url(self):
        result, jenkins, sent = self.run_flow(IOS_FAILED, ["testa-ug-app-ios", "testa-ug-app"])
        self.assertEqual((result["job_name"], result["build_number"], result["result"]), ("testa-ug-app-ios", 111, "FAILURE"))
        self.assertEqual(jenkins.console_calls[0], ("https://ugjekins.ugmid888.com/job/testa-ug-app-ios/", 111))
        self.assertIn("No profiles for 'com.ug.app'", FakeModel.requests[0][1]["messages"][1]["content"])
        chat, body, reply_to = sent[0]
        self.assertEqual((chat, reply_to), ("-1003919548725", 555))
        self.assertIn("testa-ug-app-ios</b> #111", body)
        self.assertIn("大模型 deepseek-chat", body)

    def test_service_name_only_is_resolved_to_the_job(self):
        text = IOS_FAILED.replace("Jenkins=https://ugjekins.ugmid888.com/view/testa/job/testa-ug-app-ios/111/", "Jenkins=-") \
                         .replace("发布服务名称: testa-ug-app-ios", "发布服务名称: ug-app-ios")
        result, jenkins, sent = self.run_flow(text, ["testa-ug-app-ios", "uat-ug-app-ios-prod"])
        self.assertEqual(result["job_name"], "testa-ug-app-ios")
        self.assertEqual(len(sent), 1)

    def test_manual_send_preview_and_management_chat(self):
        from cicdanalysis.__main__ import send_report
        result, jenkins, sent = self.run_flow(IOS_FAILED, ["testa-ug-app-ios"])
        app = self.app
        sent.clear()
        # Preview: nothing is sent, target is the group message the failure was announced in.
        preview = send_report(app, app.config, "testa-ug-app-ios", 111, "group", True)
        self.assertIn("chat_id=-1003919548725 reply_to=555", preview)
        self.assertIn("testa-ug-app-ios</b> #111", preview)
        self.assertEqual(sent, [])
        # Explicit resend to the group ignores the once-per-build guard.
        send_report(app, app.config, "testa-ug-app-ios", 111, "group", False)
        self.assertEqual(sent[-1][0::2], ("-1003919548725", 555))
        # Another chat gets a plain message (no reply to a group message id).
        send_report(app, app.config, "testa-ug-app-ios", 111, "-100999", False)
        self.assertEqual(sent[-1][0::2], ("-100999", None))
        with self.assertRaises(SystemExit):
            send_report(app, app.config, "testa-ug-app-ios", 111, "management", False)
        with self.assertRaises(SystemExit):
            send_report(app, app.config, "testa-ug-app-ios", 999, "group", True)


class MobileRuleTests(unittest.TestCase):
    def test_ios_and_gradle(self):
        self.assertEqual(analyze(IOS_CONSOLE)["error_code"], "IOS_SIGNING_FAILED")
        self.assertEqual(analyze("** ARCHIVE FAILED **")["error_code"], "XCODE_BUILD_FAILED")
        self.assertEqual(analyze("Execution failed for task ':app:compileReleaseKotlin'.")["error_code"], "GRADLE_TASK_FAILED")


class RealLogTests(unittest.TestCase):
    """Excerpt of testa-ug-app-ios #111 (Flutter iOS archive failed by a Dart compile error)."""

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "flutter-ios-dart-error.log"), encoding="utf-8") as f:
            self.log = f.read()

    def test_dart_error_is_the_root_cause_not_archive_failed(self):
        result = analyze(self.log, [{"name": "Flutter 构建", "status": "FAILED"}])
        self.assertEqual(result["error_code"], "COMPILE_FAILED")
        self.assertIn("app_router.dart:256:13: Error: No named parameter with the name 'platformName'", result["error_excerpt"])
        # Timestamps are stripped, so the same error on the next build has the same fingerprint.
        self.assertNotIn("2026-09-28", result["normalized_error"])
        again = analyze(self.log.replace("08:39:17", "09:12:55"), [{"name": "Flutter 构建", "status": "FAILED"}])
        self.assertEqual(result["error_fingerprint"], again["error_fingerprint"])

    def test_model_prompt_carries_the_dart_error_without_timestamps(self):
        rule = analyze(self.log)
        prompt = LLMAnalyzer("http://x", "k", "m").build_prompt({"Job": "testa-ug-app-ios", "构建号": 111}, rule, "", self.log)
        self.assertIn("No named parameter with the name 'platformName'", prompt)
        self.assertNotIn("[2026-09-28T", prompt)


class GoDockerLogTests(unittest.TestCase):
    """activity-rpc-testa #717: go build inside docker build fails on a missing import."""

    GO_LINE = "lucky_value_wager_watermark.go:13:2: package UltraGaming/services/game/provider/accountmeta is not in std"

    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "go-docker-import-error.log"), encoding="utf-8") as f:
            self.log = f.read()

    def test_go_import_error_wins_over_trailing_docker_errors(self):
        result = analyze(self.log, [{"name": "构建镜像", "status": "FAILED"}])
        self.assertEqual(result["error_category"], "CODE_ERROR")
        self.assertEqual(result["error_code"], "GO_PACKAGE_NOT_FOUND")
        self.assertEqual(result["responsibility_type"], "CODE")
        self.assertIn(self.GO_LINE, result["normalized_error"].replace("<N>", "13"))
        self.assertIn(self.GO_LINE, result["error_excerpt"])
        # BuildKit's "#12 4.154 " step/elapsed prefix is stripped, so reruns share a fingerprint.
        self.assertNotIn("4.154", result["normalized_error"])
        again = analyze(self.log.replace("#12 4.154", "#14 7.902").replace("\n4.154 ", "\n7.902 "),
                        [{"name": "构建镜像", "status": "FAILED"}])
        self.assertEqual(result["error_fingerprint"], again["error_fingerprint"])

    def test_focus_log_of_the_docker_step_also_finds_the_go_error(self):
        focus = "\n".join(x for x in self.log.splitlines() if "#1" in x or "ERROR" in x or ".go:" in x)
        result = analyze(self.log, [], focus=focus)
        self.assertEqual(result["error_code"], "GO_PACKAGE_NOT_FOUND")

    def test_model_prompt_carries_the_go_error(self):
        rule = analyze(self.log)
        prompt = LLMAnalyzer("http://x", "k", "m").build_prompt({"Job": "activity-rpc-testa", "构建号": 717}, rule, "", self.log)
        section = prompt.split("## 完整控制台日志中的报错片段")[1].split("## 控制台日志末尾")[0]
        self.assertIn(self.GO_LINE, section)
        self.assertIn("GO_PACKAGE_NOT_FOUND", prompt)
        self.assertNotIn("#12 4.154", prompt)

    def test_generic_go_compile_error_is_compile_failed(self):
        log = "#9 3.210 internal/logic/x.go:21:5: undefined: foo\n#9 ERROR: process \"/bin/sh -c go build\" did not complete successfully: exit code: 1\nERROR: failed to solve: process did not complete successfully"
        self.assertEqual(analyze(log)["error_code"], "COMPILE_FAILED")


class LLMConfigAliasTests(unittest.TestCase):
    """Existing .env files name the model settings AI_*; they must enable the model without renaming."""

    KEYS = ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MAX_TOKENS", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_MAX_TOKENS"]

    def load(self, **env):
        import importlib
        from unittest import mock
        import cicdanalysis.config as config_module
        clean = {k: v for k, v in os.environ.items() if k not in self.KEYS}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            config = importlib.reload(config_module).Config()
        importlib.reload(config_module)
        return config

    def test_ai_names_are_accepted(self):
        c = self.load(AI_API_KEY="sk-test", AI_BASE_URL="https://api.deepseek.com/v1", AI_MODEL="deepseek-reasoner", AI_MAX_TOKENS="2048")
        self.assertEqual((c.llm_api_key, c.llm_base_url, c.llm_model, c.llm_max_tokens),
                         ("sk-test", "https://api.deepseek.com/v1", "deepseek-reasoner", 2048))
        self.assertTrue(LLMAnalyzer(c.llm_base_url, c.llm_api_key, c.llm_model).enabled)

    def test_llm_names_win_and_empty_values_fall_through(self):
        c = self.load(LLM_API_KEY="", AI_API_KEY="sk-ai", LLM_MODEL="deepseek-chat", AI_MODEL="other")
        self.assertEqual((c.llm_api_key, c.llm_model, c.llm_base_url, c.llm_max_tokens),
                         ("sk-ai", "deepseek-chat", "https://api.deepseek.com", 1200))

    def test_full_endpoint_url_is_accepted(self):
        llm = LLMAnalyzer("https://api.deepseek.com/chat/completions", " sk ", "deepseek-chat")
        self.assertEqual(llm.base_url, "https://api.deepseek.com")
        self.assertEqual(llm.api_key, "sk")


class TimestampFingerprintTests(unittest.TestCase):
    def test_step_log_timestamps_never_reach_the_fingerprint(self):
        line = "lib/core/router/app_router.dart:256:13: Error: No named parameter with the name 'platformName'."
        a = analyze("", focus=f"  [2026-09-28T08:39:17.435Z] {line}")
        b = analyze("", focus=f"\ufeff[2026-09-29T11:02:03.001+08:00]{line}")
        c = analyze("", focus=line)
        self.assertNotIn("28T", a["normalized_error"])
        self.assertEqual(a["error_fingerprint"], c["error_fingerprint"])
        self.assertEqual(b["error_fingerprint"], c["error_fingerprint"])
