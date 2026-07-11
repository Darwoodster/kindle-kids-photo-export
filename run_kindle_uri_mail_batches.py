#!/usr/bin/env python3
"""Recover deterministic Amazon Kids camera batches through a local Email bridge."""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent
ADB = os.environ.get("ADB") or shutil.which("adb") or "/opt/homebrew/bin/adb"
MANIFEST = ROOT / "mail_capture" / "manifest.jsonl"
ATTACHMENTS = ROOT / "mail_capture" / "attachments"
CHECKPOINT = ROOT / "kindle_uri_mail_checkpoint.json"
MISSING_REPORT = ROOT / "kindle_email_unreadable.jsonl"
FORWARDER = ROOT / "forward_kindle_email_batch.sh"
PROTECTED_PATH_PREFIX = "/data/securedStorageLocation/com.android.camera2/FreeTime/"
EMAIL_LOWER_FS_MARKER = "Using lower FS for " + PROTECTED_PATH_PREFIX


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}")
    return result.stdout


def manifest_records() -> list[dict]:
    if not MANIFEST.exists():
        return []
    records = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_checkpoint(
    profile: str, next_offset: int, total: int, batch_size: int
) -> None:
    payload = {
        "profile": profile,
        "next_offset": next_offset,
        "total": total,
        "batch_size": batch_size,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    temporary = CHECKPOINT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CHECKPOINT)


def logcat() -> str:
    return run(ADB, "logcat", "-d", "-v", "brief")


def top_activity() -> str:
    return run(ADB, "shell", "dumpsys", "activity", "activities")


def unreadable_paths(logs: str) -> list[str]:
    return sorted(set(re.findall(r"Can't access (/data/securedStorageLocation/[^\s]+)", logs)))


def tap_send(send_x: Optional[int], send_y: Optional[int]) -> None:
    """Tap Fire Email's Send action by UI label, with optional calibrated fallback."""
    run(ADB, "shell", "uiautomator", "dump", "/sdcard/kids-photo-window.xml", check=False)
    xml_text = run(
        ADB, "exec-out", "cat", "/sdcard/kids-photo-window.xml", check=False
    )
    try:
        root = ET.fromstring(xml_text[xml_text.index("<?xml"):])
        for node in root.iter("node"):
            label = " ".join(
                [node.attrib.get("text", ""), node.attrib.get("content-desc", "")]
            ).strip().lower()
            if label == "send" or label.startswith("send "):
                match = re.fullmatch(
                    r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                    node.attrib.get("bounds", ""),
                )
                if match:
                    left, top, right, bottom = map(int, match.groups())
                    run(ADB, "shell", "input", "tap", str((left + right) // 2),
                        str((top + bottom) // 2))
                    return
    except (ValueError, ET.ParseError):
        pass
    if send_x is not None and send_y is not None:
        run(ADB, "shell", "input", "tap", str(send_x), str(send_y))
        return
    raise RuntimeError(
        "could not locate Fire Email's Send button; rerun with calibrated "
        "--send-x and --send-y coordinates"
    )


def wait_for_compose(expected: int, timeout: int) -> list[str]:
    deadline = time.monotonic() + timeout
    not_before = time.monotonic() + min(60.0, max(8.0, expected * 0.15))
    max_seen = 0
    while time.monotonic() < deadline:
        logs = logcat()
        imported = logs.count(EMAIL_LOWER_FS_MARKER)
        if imported > max_seen:
            max_seen = imported
            print(f"  Email imported at least {max_seen}/{expected} protected items", flush=True)
        if (
            time.monotonic() >= not_before
            and max_seen >= max(1, int(expected * 0.9))
            and "com.android.email/.activity.MessageCompose" in top_activity()
        ):
            return unreadable_paths(logs)
        if "Unable to prepare Email forwarding batch" in logs:
            raise RuntimeError("Kindle helper could not prepare the requested batch")
        if "FATAL EXCEPTION" in logs or "OutOfMemoryError" in logs:
            raise RuntimeError("Android process failed while importing the batch")
        time.sleep(2)
    raise TimeoutError(f"Email did not finish importing {expected} items")


def wait_for_capture(
    before: int,
    expected: int,
    offset: int,
    profile: str,
    timeout: int,
    missing_paths: list[str],
    allow_unreadable: bool,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = manifest_records()
        if len(records) > before:
            record = records[-1]
            expected_subject = f"Kindle Kids photo batch offset={offset} count={expected}"
            attachments = record.get("attachments", [])
            if record.get("subject") != expected_subject:
                raise RuntimeError(
                    f"unexpected captured subject: {record.get('subject')!r}; "
                    f"wanted {expected_subject!r}"
                )
            if len(attachments) != expected:
                shortfall = expected - len(attachments)
                remote_manifest = (
                    f"/sdcard/Download/Kindle-Kids-Photo-Export/"
                    f"forward-batch-{offset}-{expected}.tsv"
                )
                manifest_text = run(ADB, "exec-out", "cat", remote_manifest, check=False)
                expected_names = []
                for line in manifest_text.splitlines()[1:]:
                    columns = line.split("\t")
                    if len(columns) >= 4:
                        expected_names.append(columns[3])
                captured_names = {item.get("filename") for item in attachments}
                manifest_missing = [
                    PROTECTED_PATH_PREFIX + profile + "/" + name
                    for name in expected_names
                    if name not in captured_names
                ]
                if len(manifest_missing) == shortfall:
                    missing_paths = manifest_missing
                if (
                    not allow_unreadable
                    or shortfall <= 0
                    or len(missing_paths) != shortfall
                ):
                    raise RuntimeError(
                        f"captured {len(attachments)} attachments; expected {expected}; "
                        f"unreadable paths observed={missing_paths}"
                    )
                with MISSING_REPORT.open("a", encoding="utf-8") as handle:
                    for path in missing_paths:
                        handle.write(json.dumps({
                            "offset": offset,
                            "expected": expected,
                            "captured": len(attachments),
                            "path": path,
                        }) + "\n")
                print(
                    f"  Accounted for {shortfall} protected item(s) Email could not open: "
                    + ", ".join(Path(path).name for path in missing_paths),
                    flush=True,
                )
            bad = [
                item for item in attachments
                if item.get("status") not in {"saved", "duplicate_verified"}
                or int(item.get("bytes", 0)) <= 0
                or not item.get("sha256")
            ]
            if bad:
                raise RuntimeError(f"capture contained {len(bad)} invalid attachment records")
            byte_count = sum(int(item["bytes"]) for item in attachments)
            saved = sum(item["status"] == "saved" for item in attachments)
            duplicates = len(attachments) - saved
            print(
                f"  Captured {len(attachments)}/{expected}: {byte_count:,} bytes; "
                f"saved={saved}, duplicate_verified={duplicates}",
                flush=True,
            )
            return record
        time.sleep(2)
    raise TimeoutError(f"local mail bridge did not capture offset {offset}")


def process_batch(
    profile: str,
    offset: int,
    count: int,
    timeout: int,
    allow_unreadable: bool,
    send_x: Optional[int],
    send_y: Optional[int],
) -> None:
    before = len(manifest_records())
    print(f"Starting offset={offset}, count={count}", flush=True)
    run(ADB, "logcat", "-c")
    run(str(FORWARDER), profile, str(offset), str(count))
    missing_paths = wait_for_compose(count, timeout)
    # Wake the display if its short timeout elapsed, then press the fixed
    # Send action. UI discovery supports different Fire tablet resolutions.
    run(ADB, "shell", "input", "keyevent", "224")
    tap_send(send_x, send_y)
    # Fire Email sometimes leaves a large message idle until its main activity
    # is foregrounded; opening it also provides a deterministic outbox kick.
    time.sleep(2)
    run(
        ADB, "shell", "monkey", "-p", "com.android.email",
        "-c", "android.intent.category.LAUNCHER", "1",
    )
    wait_for_capture(
        before, count, offset, profile, timeout, missing_paths, allow_unreadable
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--start-offset", type=int)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--allow-unreadable", action="store_true")
    parser.add_argument("--send-x", type=int)
    parser.add_argument("--send-y", type=int)
    parser.add_argument(
        "--expected-images",
        type=int,
        help="optional final image-count assertion (omit when the future count is unknown)",
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 299:
        parser.error("--batch-size must be between 1 and 299")
    if args.total <= 0:
        parser.error("--total must be positive")
    if args.expected_images is not None and args.expected_images < 0:
        parser.error("--expected-images cannot be negative")
    if (args.send_x is None) != (args.send_y is None):
        parser.error("--send-x and --send-y must be supplied together")
    if not args.profile or "/" in args.profile or ".." in args.profile:
        parser.error("--profile must be the exact single name reported by discovery")

    run(ADB, "get-state")
    reverse = run(ADB, "reverse", "--list")
    if "tcp:2525 tcp:2525" not in reverse or "tcp:2110 tcp:2110" not in reverse:
        raise RuntimeError("required adb reverse mappings for SMTP/POP3 are missing")

    checkpoint = {}
    if CHECKPOINT.exists():
        checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        if checkpoint and checkpoint.get("profile") not in {None, args.profile}:
            raise RuntimeError("checkpoint belongs to a different Kids profile")
        if checkpoint and int(checkpoint.get("total", args.total)) != args.total:
            raise RuntimeError("checkpoint total differs from --total")
    offset = (
        args.start_offset
        if args.start_offset is not None
        else int(checkpoint.get("next_offset", 0))
    )
    if not 0 <= offset <= args.total:
        parser.error("start offset is outside the requested total")

    while offset < args.total:
        count = min(args.batch_size, args.total - offset)
        process_batch(
            args.profile, offset, count, args.timeout, args.allow_unreadable,
            args.send_x, args.send_y,
        )
        offset += count
        write_checkpoint(args.profile, offset, args.total, args.batch_size)
        print(f"Checkpoint advanced to {offset}/{args.total}", flush=True)

    files = [path for path in ATTACHMENTS.iterdir() if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    print(
        f"COMPLETE unique_files={len(files)} total_expected={args.total} "
        f"bytes={total_bytes:,}",
        flush=True,
    )
    missing = set()
    if MISSING_REPORT.exists():
        for line in MISSING_REPORT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                missing.add(json.loads(line)["path"])
    accounted = len(files) + len(missing)
    image_count = sum(path.suffix.lower() != ".mp4" for path in files)
    print(
        f"ACCOUNTED local_files={len(files)} unreadable={len(missing)} "
        f"accounted={accounted}/{args.total} images={image_count}",
        flush=True,
    )
    if accounted != args.total:
        raise RuntimeError(
            f"final accounted count {accounted} does not match expected {args.total}"
        )
    if args.expected_images is not None and image_count != args.expected_images:
        raise RuntimeError(
            f"final image count {image_count} does not match expected "
            f"{args.expected_images}"
        )


if __name__ == "__main__":
    main()
