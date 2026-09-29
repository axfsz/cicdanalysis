import unittest

from cicdanalysis.analyzer import analyze, redact


class AnalyzerTests(unittest.TestCase):
    def test_pnpm(self):
        r=analyze("ERR_PNPM_OUTDATED_LOCKFILE Cannot install with frozen-lockfile")
        self.assertEqual(r["error_code"],"ERR_PNPM_OUTDATED_LOCKFILE")
        self.assertEqual(r["responsibility_type"],"CODE")

    def test_k8s(self):
        r=analyze("error: deployment progress deadline exceeded",[{"name":"Deploy","status":"FAILED"}])
        self.assertEqual(r["error_category"],"K8S_ERROR")
        self.assertEqual(r["failed_stage"],"Deploy")

    def test_redaction(self):
        safe=redact("Authorization: Bearer abcdef token=supersecret password: hello")
        self.assertNotIn("abcdef",safe);self.assertNotIn("supersecret",safe);self.assertNotIn("hello",safe)

    def test_stable_fingerprint(self):
        a=analyze("Connection timed out at 123456 https://one.local/a")
        b=analyze("Connection timed out at 987654 https://two.local/b")
        self.assertEqual(a["error_fingerprint"],b["error_fingerprint"])


if __name__=="__main__":unittest.main()
