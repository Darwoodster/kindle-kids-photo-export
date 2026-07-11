# Technical explanation

Amazon Kids camera files can be indexed by Android MediaStore while their
underlying path remains protected from ordinary MTP/USB browsing. The helper is
installed in Android user 0 and queries image/video rows whose `_data` path is
under Fire OS's Amazon Kids camera root.

For each deterministic batch it:

1. Sorts MediaStore rows by `date_added`, newest first, with stable tie-breaks.
2. Converts image and video IDs into their standard MediaStore content URIs.
3. Grants Fire Email temporary read permission for those URIs and supplies them
   through `ACTION_SEND_MULTIPLE` plus `ClipData`.
4. Writes a TSV batch manifest to public Downloads for shortfall accounting.

Fire Email is able to open most protected content through the granted URI even
though a normal file copy cannot traverse the underlying path. Its SMTP and
POP3 settings point to `127.0.0.1`. ADB reverse maps those tablet-side loopback
ports to a Python server bound to loopback on the computer, so the message never
uses Wi-Fi or an external mail service.

The Python bridge writes every raw message and hashes each decoded attachment.
The batch runner validates subject, count, non-zero length, and SHA-256 before
advancing an atomic checkpoint. If Fire Email cannot open a protected item, a
shortfall is accepted only when the missing filename can be reconciled with the
helper's batch TSV and the protected-path error in logcat.

The method intentionally does not root the tablet, bypass the lock screen,
modify the child profile, or delete originals.
