import os
import tempfile
import unittest

from cicdanalysis.app import App
from cicdanalysis.config import Config


class FakeTelegram:
    def __init__(self): self.calls=[]
    def send(self,chat_id,text): self.calls.append((chat_id,text));return True


class ReportDeliveryTests(unittest.TestCase):
    def test_each_environment_group_receives_once(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(Config(database_path=os.path.join(directory,"test.db"),database_url=""))
            fake=FakeTelegram();app.telegram=fake
            self.assertTrue(app.send_reports("daily","2026-09-08"))
            self.assertEqual([c[0] for c in fake.calls],["-1003919548725","-5592801576","-1003412281586"])
            self.assertIn("TESTA",fake.calls[0][1]);self.assertIn("UAT",fake.calls[1][1]);self.assertIn("PROD",fake.calls[2][1])
            self.assertTrue(app.send_reports("daily","2026-09-08"))
            self.assertEqual(len(fake.calls),3)
            self.assertEqual(len(app.db.query("SELECT * FROM report_deliveries")),3)


if __name__=="__main__":unittest.main()
