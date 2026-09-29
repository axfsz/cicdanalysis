import json
import unittest

from cicdanalysis.openapi import VERSION, openapi_json, openapi_spec, redoc_html, swagger_ui_html


class OpenAPITests(unittest.TestCase):
    def test_contract_covers_every_public_api(self):
        spec = openapi_spec()
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertEqual(spec["info"]["version"], VERSION)
        self.assertEqual(
            set(spec["paths"]),
            {
                "/healthz",
                "/readyz",
                "/metrics",
                "/api/v1/overview",
                "/api/v1/builds",
                "/api/v1/users",
                "/api/v1/failures",
                "/api/v1/triggers",
                "/api/v1/webhooks/jenkins",
                "/api/v1/webhooks/trigger",
                "/api/v1/webhooks/release-message",
                "/api/v1/admin/telegram-backfill",
            },
        )

    def test_webhook_documents_authentication_and_examples(self):
        operation = openapi_spec()["paths"]["/api/v1/webhooks/jenkins"]["post"]
        self.assertEqual(operation["security"], [{"WebhookBearer": []}])
        self.assertIn("202", operation["responses"])
        self.assertIn("401", operation["responses"])
        self.assertEqual(operation["requestBody"]["content"]["application/json"]["example"]["build_number"], 1922)

    def test_document_does_not_contain_runtime_secrets(self):
        raw = openapi_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["info"]["title"], "cicdanalysis API")
        self.assertNotIn(b"JENKINS_API_TOKEN=", raw)
        self.assertNotIn(b"TELEGRAM_BOT_TOKEN=", raw)

    def test_documentation_pages_load_openapi_contract(self):
        self.assertIn("/openapi.json", swagger_ui_html())
        self.assertIn("SwaggerUIBundle", swagger_ui_html())
        self.assertIn("/openapi.json", redoc_html())
        self.assertIn("<redoc", redoc_html())


if __name__ == "__main__":
    unittest.main()
