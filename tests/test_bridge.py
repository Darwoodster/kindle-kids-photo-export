#!/usr/bin/env python3
import hashlib
import json
import poplib
import smtplib
import tempfile
import threading
import unittest
from email.message import EmailMessage
from pathlib import Path

from local_mail_bridge import BridgeState, POP3Handler, SMTPHandler, ThreadingServer


class BridgeTest(unittest.TestCase):
    def test_smtp_attachment_and_empty_pop3(self):
        with tempfile.TemporaryDirectory() as directory:
            state = BridgeState(Path(directory))
            smtp = ThreadingServer(("127.0.0.1", 0), SMTPHandler)
            pop = ThreadingServer(("127.0.0.1", 0), POP3Handler)
            smtp.state = state
            pop.state = state
            threads = [
                threading.Thread(target=smtp.serve_forever, daemon=True),
                threading.Thread(target=pop.serve_forever, daemon=True),
            ]
            for thread in threads:
                thread.start()
            try:
                payload = b"local-only-smoke-test"
                message = EmailMessage()
                message["From"] = "export@device.invalid"
                message["To"] = "capture@local.invalid"
                message["Subject"] = "bridge test"
                message.set_content("test")
                message.add_attachment(payload, maintype="application",
                                       subtype="octet-stream", filename="test.bin")
                with smtplib.SMTP("127.0.0.1", smtp.server_address[1]) as client:
                    client.login("throwaway", "generated-locally")
                    client.send_message(message)

                record = json.loads(
                    (Path(directory) / "manifest.jsonl").read_text().strip()
                )
                attachment = record["attachments"][0]
                self.assertEqual(attachment["filename"], "test.bin")
                self.assertEqual(attachment["bytes"], len(payload))
                self.assertEqual(attachment["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(
                    (Path(directory) / "attachments" / "test.bin").read_bytes(), payload
                )

                client = poplib.POP3("127.0.0.1", pop.server_address[1])
                client.user("throwaway")
                client.pass_("generated-locally")
                self.assertEqual(client.stat(), (0, 0))
                client.quit()
            finally:
                smtp.shutdown()
                pop.shutdown()
                smtp.server_close()
                pop.server_close()


if __name__ == "__main__":
    unittest.main()
