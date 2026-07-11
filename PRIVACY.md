# Privacy and security

- The capture server binds only to `127.0.0.1` on the computer.
- The tablet reaches it through temporary ADB reverse tunnels over USB.
- The helper APK does not request Android internet permission.
- No analytics, telemetry, cloud API, real mail server, or remote account is
  used.
- Profile names, filenames, raw messages, hashes, and recovered media remain in
  the local `mail_capture/` directory, which Git ignores.
- The documented `.invalid` addresses are reserved non-routable examples.
- The local bridge accepts any password. Generate a throwaway value and never
  reuse a real credential.
- USB debugging gives the authorized computer powerful access to the tablet.
  Remove the ADB tunnels and disable debugging when finished.

Before publishing logs in an issue, remove child-profile names, filenames,
device serial numbers, email addresses, local paths, and photo/video content.
Do not attach `manifest.jsonl`, raw `.eml` files, or screenshots containing a
child's media.
