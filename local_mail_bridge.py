#!/usr/bin/env python3
"""Local-only SMTP/POP bridge for recovering Fire Kids media over ADB reverse."""

import argparse
import email
import hashlib
import json
import re
import signal
import socketserver
import threading
import time
from email import policy
from pathlib import Path


class BridgeState:
    def __init__(self, root: Path):
        self.root = root
        self.messages = root / "messages"
        self.attachments = root / "attachments"
        self.root.mkdir(parents=True, exist_ok=True)
        self.messages.mkdir(parents=True, exist_ok=True)
        self.attachments.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.message_count = 0
        self.attachment_count = 0
        self.total_bytes = 0

    @staticmethod
    def safe_name(value: str) -> str:
        name = Path(value or "attachment.bin").name
        name = re.sub(r"[\x00-\x1f/:]", "_", name).strip()
        return name or "attachment.bin"

    def save_message(self, raw: bytes, envelope: dict):
        with self.lock:
            self.message_count += 1
            number = self.message_count
            stamp = time.strftime("%Y%m%d-%H%M%S")
            message_path = self.messages / f"{stamp}-{number:04d}.eml"
            message_path.write_bytes(raw)

            msg = email.message_from_bytes(raw, policy=policy.default)
            saved = []
            for part in msg.walk():
                filename = part.get_filename()
                if not filename:
                    continue
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                name = self.safe_name(filename)
                digest = hashlib.sha256(payload).hexdigest()
                target = self.attachments / name
                status = "saved"
                if target.exists():
                    existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    if existing_digest == digest:
                        status = "duplicate_verified"
                    else:
                        stem, suffix = target.stem, target.suffix
                        target = self.attachments / f"{stem}__{digest[:10]}{suffix}"
                if status == "saved":
                    target.write_bytes(payload)
                    self.attachment_count += 1
                    self.total_bytes += len(payload)
                saved.append({
                    "filename": target.name,
                    "bytes": len(payload),
                    "sha256": digest,
                    "status": status,
                })

            record = {
                "message": message_path.name,
                "mail_from": envelope.get("mail_from"),
                "rcpt_to": envelope.get("rcpt_to", []),
                "subject": str(msg.get("subject", "")),
                "attachments": saved,
            }
            with (self.root / "manifest.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f"CAPTURED message={number} attachments={len(saved)} "
                f"unique_total={self.attachment_count} bytes_total={self.total_bytes}",
                flush=True,
            )


class SMTPHandler(socketserver.StreamRequestHandler):
    def send(self, line: str):
        self.wfile.write((line + "\r\n").encode("ascii"))
        self.wfile.flush()

    def handle(self):
        envelope = {"mail_from": None, "rcpt_to": []}
        self.send("220 fire-photo-bridge.invalid ESMTP ready")
        while True:
            raw = self.rfile.readline(65537)
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            command, _, argument = line.partition(" ")
            command = command.upper()
            if command in ("EHLO", "HELO"):
                self.send("250-fire-photo-bridge.invalid")
                # Advertise Android's maximum signed-int message size so the
                # local recovery account does not inherit Email's 10 MB default.
                self.send("250-SIZE 2147483647")
                self.send("250-8BITMIME")
                self.send("250-AUTH PLAIN LOGIN")
                self.send("250 OK")
            elif command == "AUTH":
                method, _, initial = argument.partition(" ")
                if method.upper() == "LOGIN" and not initial:
                    self.send("334 VXNlcm5hbWU6")
                    self.rfile.readline(65537)
                    self.send("334 UGFzc3dvcmQ6")
                    self.rfile.readline(65537)
                self.send("235 2.7.0 Authentication successful")
            elif command == "MAIL":
                envelope = {"mail_from": argument, "rcpt_to": []}
                self.send("250 2.1.0 OK")
            elif command == "RCPT":
                envelope["rcpt_to"].append(argument)
                self.send("250 2.1.5 OK")
            elif command == "DATA":
                self.send("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    data_line = self.rfile.readline(1048577)
                    if not data_line or data_line in (b".\r\n", b".\n"):
                        break
                    if data_line.startswith(b".."):
                        data_line = data_line[1:]
                    chunks.append(data_line)
                self.server.state.save_message(b"".join(chunks), envelope)
                self.send("250 2.0.0 Captured locally")
            elif command == "RSET":
                envelope = {"mail_from": None, "rcpt_to": []}
                self.send("250 2.0.0 OK")
            elif command == "NOOP":
                self.send("250 2.0.0 OK")
            elif command == "QUIT":
                self.send("221 2.0.0 Bye")
                return
            else:
                self.send("250 OK")


class POP3Handler(socketserver.StreamRequestHandler):
    def send(self, line: str):
        self.wfile.write((line + "\r\n").encode("ascii"))
        self.wfile.flush()

    def handle(self):
        self.send("+OK Fire photo bridge POP3 ready")
        while True:
            raw = self.rfile.readline(65537)
            if not raw:
                return
            command = raw.decode("utf-8", "replace").strip().split(" ", 1)[0].upper()
            if command == "CAPA":
                self.send("+OK Capability list follows")
                self.send("USER")
                self.send("UIDL")
                self.send("TOP")
                self.send(".")
            elif command in ("USER", "PASS", "NOOP"):
                self.send("+OK")
            elif command == "STAT":
                self.send("+OK 0 0")
            elif command in ("LIST", "UIDL"):
                self.send("+OK 0 messages")
                self.send(".")
            elif command == "QUIT":
                self.send("+OK Bye")
                return
            else:
                self.send("-ERR no messages")


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--smtp-port", type=int, default=2525)
    parser.add_argument("--pop-port", type=int, default=2110)
    args = parser.parse_args()

    state = BridgeState(args.root)
    smtp = ThreadingServer(("127.0.0.1", args.smtp_port), SMTPHandler)
    pop = ThreadingServer(("127.0.0.1", args.pop_port), POP3Handler)
    smtp.state = state
    pop.state = state

    threads = [
        threading.Thread(target=smtp.serve_forever, name="smtp", daemon=True),
        threading.Thread(target=pop.serve_forever, name="pop3", daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(
        f"READY smtp=127.0.0.1:{args.smtp_port} pop3=127.0.0.1:{args.pop_port} "
        f"root={args.root}",
        flush=True,
    )

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1):
        pass
    smtp.shutdown()
    pop.shutdown()


if __name__ == "__main__":
    main()
