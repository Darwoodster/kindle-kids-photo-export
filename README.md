# Export photos from an Amazon Kids profile on a Kindle Fire tablet

Recover the original photos and videos taken inside an **Amazon Kids child
profile** when ordinary USB file transfer shows an empty camera folder.

This is an unofficial, local-only macOS workflow for Fire OS. A small open
source helper grants Fire Email temporary read access to the child's protected
camera items. Fire Email sends them to a tiny SMTP capture server on your Mac
through an **ADB USB reverse tunnel**. Nothing is uploaded to Amazon, an email
provider, or the internet.

> This is an involved recovery method for media you own and can view locally.
> Test with one photo first. It never needs root access and the supplied code
> never deletes media from the tablet.

## Why normal USB transfer does not show the photos

Recent Fire tablets keep Amazon Kids camera media in a protected per-profile
location. The adult profile's USB/MTP view can be working normally while those
files remain absent. Gallery and Fire Email can still receive temporary Android
content-URI permission, which is the route used here.

## Requirements

- A Fire tablet where the photos are visible in the child's Amazon Kids Gallery
- The adult PIN/password and physical access to the tablet
- A USB data cable
- macOS with Python 3, Homebrew, and Android Platform Tools
- The tablet's built-in Fire Email app

Install ADB on the Mac:

```sh
brew install android-platform-tools
```

Download this repository, open Terminal in it, and make the scripts executable:

```sh
chmod +x forward_kindle_email_batch.sh run_kindle_uri_mail_batches.py
```

Use the release APK if one is provided, or follow [BUILDING.md](BUILDING.md) to
build it from the auditable Java source.

Verify a downloaded repository APK before installation:

```sh
shasum -a 256 -c SHA256SUMS
```

## Step 1: enable ADB and install the helper

Switch to the adult profile. Open **Settings > Device Options**. If Developer
Options is hidden, tap **Serial Number** seven times. Open Developer Options,
enable **USB debugging**, connect the USB cable, and approve the Mac on the
tablet.

```sh
adb devices
```

Continue only when one device is listed as `device`, not `unauthorized`.

Install the helper APK:

```sh
adb install -r -g kindle-kids-photo-export.apk
```

The package is `io.github.kindlekidsphotoexport`. The helper has no internet
permission and does not delete files.

## Step 2: create the USB-only local mail connection

Create the two temporary tunnels:

```sh
adb reverse tcp:2525 tcp:2525
adb reverse tcp:2110 tcp:2110
adb reverse --list
```

The final command must show both mappings. In Terminal window 1, start the
loopback-only capture server and leave it running:

```sh
python3 local_mail_bridge.py --root mail_capture
```

Wait for `READY`. The server listens only on `127.0.0.1`; ADB makes that local
server appear at the same loopback address to the tablet.

## Step 3: add a throwaway local account to Fire Email

Generate a password unique to this temporary account:

```sh
openssl rand -hex 16
```

In the adult profile, add an **Other** account in Fire Email and choose manual
POP3 setup. The values below are deliberately non-routable; this is not a real
email account.

| Setting | Value |
|---|---|
| Address / username | `export@device.invalid` |
| Password | The newly generated value above |
| Incoming type | POP3 |
| Incoming server | `127.0.0.1` |
| Incoming port | `2110` |
| Incoming security | None / SSL off |
| Outgoing server | `127.0.0.1` |
| Outgoing port | `2525` |
| Outgoing security | None / SSL off |
| Outgoing authentication | On, using the same username and password |

The bridge intentionally accepts any credentials because it is reachable only
through the local USB tunnel. Do not reuse a real password.

## Step 4: discover the child-profile directory name

The visible Kids profile name and its protected directory name are usually the
same, but discover it rather than guessing:

```sh
adb logcat -c
adb shell am start --user 0 -S -W \
  -n io.github.kindlekidsphotoexport/.ExportActivity \
  --ez list_profiles true
adb logcat -d -s KidsPhotoExport:I '*:S'
```

Each result looks like:

```text
PROFILE_FOUND name=CHILD_PROFILE_NAME total=NUMBER
```

Keep the exact `name` and `total`. No profile name is transmitted off the Mac
or tablet.

## Step 5: test one original photo

Terminal window 1 must still show the capture bridge running. In Terminal
window 2, replace `CHILD_PROFILE_NAME` exactly, preserving quotes if it contains
spaces:

```sh
./forward_kindle_email_batch.sh 'CHILD_PROFILE_NAME' 0 1
```

Fire Email opens a draft containing one original item. Press **Send**. Terminal
window 1 should report one captured attachment. Verify it exists in:

```text
mail_capture/attachments/
```

The helper orders items newest first, matching the usual beginning of Gallery.
This one-item test becomes a verified duplicate when the automated full run
reaches offset 0; it will not overwrite different content.

## Step 6: export everything in resumable batches

Use batches of 100. Although Fire Gallery was observed to allow up to 299
selected items, the older Fire Email app was less reliable with larger drafts.

```sh
./run_kindle_uri_mail_batches.py \
  --profile 'CHILD_PROFILE_NAME' \
  --total NUMBER \
  --batch-size 100 \
  --start-offset 0 \
  --allow-unreadable
```

The runner waits for each draft, locates the visible **Send** action, taps it,
foregrounds Fire Email to wake its outbox, validates the received attachments,
and writes a checkpoint. Keep the tablet connected, unlocked, and in the same
orientation.

If automatic Send-button detection fails, inspect the tablet resolution and
rerun with the button's coordinates, for example:

```sh
adb shell wm size
./run_kindle_uri_mail_batches.py \
  --profile 'CHILD_PROFILE_NAME' --total NUMBER --batch-size 100 \
  --start-offset 0 --allow-unreadable --send-x X --send-y Y
```

To resume after an interruption, omit `--start-offset`; the runner reads
`kindle_uri_mail_checkpoint.json`:

```sh
./run_kindle_uri_mail_batches.py \
  --profile 'CHILD_PROFILE_NAME' --total NUMBER --batch-size 100 \
  --allow-unreadable
```

## Step 7: verify completion

Do not rely only on the number of files in Finder. The final output must include:

```text
COMPLETE unique_files=... total_expected=... bytes=...
ACCOUNTED local_files=... unreadable=... accounted=NUMBER/NUMBER images=...
```

Every received attachment is stored in `mail_capture/attachments/`. The bridge
also keeps each raw local message and appends filename, byte count, SHA-256, and
status to `mail_capture/manifest.jsonl`. Existing filenames count as duplicates
only when their bytes hash identically.

Some protected videos can be visible in Gallery but unreadable by Fire Email.
`--allow-unreadable` accepts a shortfall only when the runner can identify the
exact protected path from both the batch manifest and Fire Email logs. It records
those paths in `kindle_email_unreadable.jsonl` instead of silently claiming they
were recovered.

## Step 8: shut everything down safely

Stop the capture server with Control-C, then remove the tunnels:

```sh
adb reverse --remove-all
adb reverse --list
```

Disable USB debugging and Developer Options on the tablet. You may uninstall
the helper and remove the throwaway Email account, or retain them with debugging
disabled for a future export:

```sh
adb uninstall io.github.kindlekidsphotoexport
```

Keep at least two independent backups of the recovered originals before making
any changes to the tablet.

## Troubleshooting

- **No profiles found:** confirm the photos are visible in the child Gallery,
  the helper was installed with `-g`, and the command used Android user 0.
- **Email account setup fails:** ensure the Python bridge is running and both
  ADB reverse mappings exist before adding the account.
- **Message remains in Outbox:** wait. The runner foregrounds Email to kick the
  queue. Do not press Send repeatedly.
- **Fire Email crashes:** reduce `--batch-size` to 50 and resume from the same
  checkpoint.
- **Capture count is short:** do not skip validation. Read
  `kindle_email_unreadable.jsonl` and the latest manifest entry.
- **More than one child profile:** export each discovered profile separately to
  a fresh `mail_capture` directory.

See [Technical explanation](docs/TECHNICAL.md), [Privacy and security](PRIVACY.md),
and [Building from source](BUILDING.md).

## Scope and disclaimer

This project is not affiliated with or endorsed by Amazon. Fire OS versions and
Email UI layouts vary. Use it only on a tablet and media you are authorized to
access. The maintainers cannot recover files from a damaged or reset device.
