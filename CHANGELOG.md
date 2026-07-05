# Changelog

All notable changes to VRD Next are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

## [1.3.0] - 2026-07-05

### Added

- **Light and dark themes.** A **Theme** setting (Follow system, Light or Dark)
  under **Settings → General**, applied live without a restart. The Light theme
  echoes the classic VideoReDo look; the timeline and thumbnail bars stay dark in
  every theme by design.
- **HEVC output.** Output profiles gain a **Video** option — **Copy** (the
  lossless default) or **HEVC (re-encode)** to H.265 for much smaller files. It's
  opt-in per profile, and the lossless cutting path is unchanged when it's set to
  Copy. Interlaced sources are deinterlaced when re-encoding to HEVC.
- **Renamer presets.** Save your own naming patterns as named presets in the TV
  and Film renamers (each keeps its own list), with **Save…** and **Delete**.
- **More input formats.** `.mp4`, `.m2ts`, `.mov` and `.avi` can be opened
  alongside `.ts` and `.mkv`, with an "All files" fall-back.
- **Per-channel Comskip `.ini`.** When a recording's filename contains the
  channel name (for example Tvheadend, via its `$c`), the watcher and manual
  detection can pick a `Comskip_<channel>.ini` from beside the main `.ini` — the
  longest, case-insensitive match winning. Enabled under
  **Settings → External tools**.
- **One-step installers.** `packaging/install-linux.sh` (Debian/Ubuntu/Mint) and
  `packaging/install-windows.ps1` (Windows, via winget) set up the dependencies, a
  virtual environment and menu/desktop shortcuts. A multi-resolution app icon is
  included for the shortcuts.

### Changed

- Theme changes now apply live across the whole interface — chrome, transport
  readouts, icons and buttons — rather than needing a restart.

### Fixed

- A crash (stack overflow) when switching theme, caused by a palette-change
  recursion in the transport panel.
- The TV renamer no longer misreads an episode whose title looks like an episode
  number — for example a title of "E2" after `S03E21` — as a two-parter.

## [1.2.0] - 2026-07-02

### Added

- **In-app user guide.** A full illustrated user guide, reached from
  **Help → User Guide**, walks through the editor, cutting, profiles, the
  renamers, Comskip and the watcher/batch, with annotated screenshots.
- **External tool paths.** A new **Settings → External tools** page for
  ffmpeg, ffprobe, mkvmerge and Comskip. Paths are auto-detected from your `PATH`,
  or you can point at a specific build (for example a newer ffmpeg than your
  distribution ships). You're warned if a required tool is missing, or if a path
  you've set no longer exists.
- **Rename/move logging.** Every operation in the TV and Film renamers is now
  recorded in the application log, so you can see exactly where each finished file
  ended up.
- **Source information in the log.** Opening a file now writes an ffprobe-style
  summary (container, codecs, stream layout and timing) to the log, to help with
  troubleshooting.

### Changed

- The renamers' **"Rename Ticked"** button is now **"Process Ticked"**, since the
  step may move files as well as rename them.
- **Open Recent** now keeps entries whose source has since been moved or deleted,
  showing them greyed-out and marked "(missing)" rather than dropping them.

### Fixed

- **Preview audio on Blu-ray and other disc rips.** Audio now plays reliably on
  MKV rips (DTS, DTS-HD MA, AC3) that previously fell silent when seeking. The
  seek strategy is chosen to suit the source, so broadcast recordings keep their
  tight, in-sync preview while disc rips seek by the video index. Exported cuts
  are made straight from the source and stay perfectly in sync regardless of the
  preview.
- Maximising or resizing the window during playback now updates the picture
  smoothly, instead of leaving it briefly at the old size.

## [1.1.0] - 2026-06-27

### Added

- **TMDB episode picker.** Double-click a matched row in the TV renamer to choose
  the exact season and episode from a dialog that fetches the show's seasons and
  episodes from TMDB. Ctrl-click to select two episodes for a two-parter, use
  "Change show…" if the auto-match was wrong, and Season 0 is presented as
  "Specials".
- **Pixel cropping.** A new per-profile option to remove letterbox or pillarbox
  black bars. Because the bars are baked into the picture, cropping re-encodes the
  video (the only non-lossless step in the tool); the normal lossless cutting path
  is untouched when cropping is off.
  - **Auto-detect** finds the bars per file at export time, or set **Fixed pixels**
    by hand.
  - A **preview window** shows a real frame from the open recording with the crop
    shaded in, a slider to skim for a frame to crop against, live edge adjustment,
    and an Auto-detect button.
  - The re-encode matches the source's own bitrate as a ceiling and uses constant
    quality (CRF), so a cropped file is never larger than the source and is usually
    smaller.
  - The **scan type is preserved** — an interlaced source stays interlaced (with
    its original field order), a progressive source stays progressive.
  - Cropping works in the watcher/batch pipeline as well as manual exports, with a
    proper progress bar and accurate timing for the re-encode stage.

### Changed

- The TV renamer now files Season 0 episodes into a "Specials" folder.
- Versioning tidied up: the old build-number field has been retired in favour of a
  `build_stamp()` derived from the version string.

## [1.0.0]

- Initial public release.

[1.3.0]: https://github.com/infidelus/vrd-next/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/infidelus/vrd-next/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/infidelus/vrd-next/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/infidelus/vrd-next/releases/tag/v1.0.0
