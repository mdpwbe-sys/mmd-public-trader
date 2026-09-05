# Windows distribution and Defender review

MMD Trader is built with stock PyInstaller. The release build explicitly uses
`--noupx`; it does not invoke UPX or any other executable packer/compressor.
`build_exe.py --onedir` produces `dist/MMD-Trader/MMD-Trader.exe` for a direct
Defender comparison with the onefile release artifact.

## Release process

1. Build the onefile release: `python build_exe.py`.
2. Build the comparison variant: `python build_exe.py --onedir`.
3. Run `release_check.ps1` on the onefile artifact.
4. Sign the final, unchanged binary with `sign_release.ps1`, then run
   `release_check.ps1` again. The signer writes a SHA-256 sidecar used to detect
   a post-signing modification.

`MMD_PUBLISHER` and `MMD_COMPANY_NAME` can be supplied as environment variables
at build time. The Windows VERSIONINFO is generated from `version.py`.

## Runtime heuristic review

| Area | Runtime behavior | Mitigation / rationale |
| --- | --- | --- |
| Subprocess / shell | No MMD GUI runtime shell execution. | The old PowerShell clipboard fallback was removed; copying uses the existing Win32 clipboard API with short retries. |
| Downloads | ESI, zKillboard and R2Z2 fetch JSON only. | The application does not download and execute binaries or scripts. |
| TEMP writes | PyInstaller onefile extracts its own bundle to TEMP. | The onedir build is provided for Defender comparison. Persistent app data uses `%APPDATA%/MMD-Trader`. |
| Win32 / ctypes | Clipboard, window drag/topmost and native notifications. | These are direct user-facing desktop operations, bounded and exception-safe. |
| Clipboard watcher | Polls the clipboard sequence and reads text after an EVE copy. | It does not transmit clipboard contents and rejects non-Local text. |
| Auto-update | None. | MMD does not self-update or replace its executable. |

Code signing and download reputation remain the primary way to reduce SmartScreen
and Defender false positives; no build flag can guarantee a clean reputation.
