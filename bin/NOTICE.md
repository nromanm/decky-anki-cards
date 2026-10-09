# Bundled third-party binaries

## `tesseract`

Static, musl-linked Tesseract OCR 5.5.3 binary for Linux x86_64, used because SteamOS's own
glibc (2.41) is older than what current official Arch Linux packages require (2.43+) — a static
build sidesteps the mismatch entirely. Source: the official GitHub Actions build pipeline at
[DanielMYT/tesseract-static](https://github.com/DanielMYT/tesseract-static) (release
`tesseract-5.5.3-rebuild`), which builds Tesseract and its dependencies (Leptonica, etc.) from
their own upstream sources via a public, auditable workflow
(`.github/workflows/build.yml`) rather than hand-distributing a prebuilt binary from an unknown
process. License terms for Tesseract itself and its statically-linked dependencies are in
`license.tesseract.txt` and `license.libraries.txt` (both from that release), all Apache 2.0 /
permissive.

## `tessdata/*.traineddata`

Fetched directly from the Tesseract project's own
[tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) repository (the lightweight,
fast-inference model variant, as opposed to the larger "best accuracy" models) — same license as
Tesseract itself (Apache 2.0). Only the languages already offered by this plugin's language
selectors are included.
