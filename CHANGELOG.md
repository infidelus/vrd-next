# Changelog

All notable changes to VRD Next are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

## [1.7.0] - 2026-07-14

### Added

- **Lossless LATM audio passthru.** UK broadcast AAC (LATM/LOAS framing) is now
  unwrapped to plain AAC as the cut is made — byte-for-byte identical audio,
  verified on real BBC recordings including HE-AACv2 audio-description tracks —
  so the post-cut audio graft no longer runs in the common case and stays only
  as a verified fallback. Mid-programme configuration changes are handled
  transparently.
- **Drag and drop.** Drop a video onto the window to open it, several to fill
  the joiner, or a `.vprj` to open its project.
- **"Open with VRD Next" opens the file.** File-manager launches now load the
  passed file (previously the app opened empty), and the desktop registration
  covers `.mp4`, `.mpg`, `.mov`, `.avi` and `.vprj` as well as `.ts`/`.mkv`.

### Changed

- **Packet interleaving moved into the cut**, retiring the separate post-export
  re-interleave pass — exports finish sooner. The `.ts` finalise step now runs
  only when there are audio dispositions to restore.
- **Audio-description labelling mirrors the source faithfully** (language and
  dispositions, including the visual-impaired flag, with no invented track
  names), consolidated into one helper shared by all container writers. This
  also fixes the main track showing as a bare index when only the description
  track was named.
- Batch Manager: the progress bar seeds immediately when opened mid-run; queued
  jobs can be reordered during a run; Clear Finished works mid-run.

### Fixed

- **Interlaced cut boundaries** on 1080i and 576i recordings were re-encoded
  progressive, causing combing on motion for the re-encoded stretch after each
  cut. The boundary encoder now matches the source's interlacing, handling
  mixed MBAFF content frame by frame; progressive sources are unaffected.
- **Quick Stream Fix**: no longer strips secondary audio tracks, fails on data
  or unrecognised streams, displaces audio-description timing, or fails on
  recordings with a declared-but-dead audio PID ("sample rate not set").
- The chosen output profile is remembered across a Quick Stream Fix instead of
  resetting to the last container match.

## [1.6.0] - 2026-07-12

### Added

- **Open several files at once.** Selecting more than one file in
  File → Open Video brings up an Open Multiple Files dialogue (modelled on
  VideoReDo's): reorder by dragging or sort by name, then every file is added
  — whole — to the Joiner list and the Joiner opens.
- **Multi-track lossless audio.** The broadcast-audio graft now covers
  recordings with more than one audio track, so Channel 4 HD-style
  audio-description tracks are kept losslessly and in sync instead of being
  dropped by the re-encode fallback. A safety net verifies every grafted track
  decodes, falling back to the previous behaviour if anything is off.
- **Audio-description labelling in every container.** The "visual impaired"
  marking a .ts carries is now re-stated on export: MKV gets Matroska's
  visual-impaired flag plus a track name, MP4 gets the name in its handler
  atom, so players label the track as the broadcaster intended.

### Fixed

- **Quick Stream Fix no longer strips secondary audio tracks** (it kept only
  one stream per type), no longer fails outright on recordings carrying data
  or unrecognised streams (SCTE-35 splice markers, EPG oddments — these are
  now skipped), and **no longer displaces audio-description timing**: ffmpeg's
  discontinuity correction misread the AD track's legitimate transmission gaps
  during ad breaks and shifted its narration by minutes; QSF now preserves
  every stream's timing exactly.
- **Audio dropouts in Kodi/VLC/Jellyfin after cutting.** Exports could leave a
  long run of video-only packets at a cut seam with the matching audio muxed
  many seconds later; players with small demux buffers played silent video
  until the skew passed. Finished .ts exports now get a fast lossless
  re-interleave pass. (Existing affected files can be repaired by running
  Quick Stream Fix on them.)
- **File dialogs were case-sensitive on Linux**, hiding upper-case names such
  as `VIDEO.MP4` until the filter was switched to All files. Every open/save
  dialog now matches both cases.

## [1.5.1] - 2026-07-11

### Changed

- **Create Video From Joiner List** now uses the same Save Video dialogue as
  everywhere else, with full output profiles, matching VideoReDo. A plain
  lossless-copy profile takes the proven fast path (container change only,
  byte-for-byte video, per-scene MKV chapters preserved); a profile that
  processes the picture or audio (HEVC, crop, aspect, or AAC re-encode) is
  applied to the joined result in a single whole-file pass, so the output is
  identical to what Save Video produces for the same profile.
- The Linux installer's virtual environment is now built on Python 3.12,
  installs the Qt runtime libraries the PySide6 wheels need (notably
  `libxcb-cursor0`, without which the application starts and then dies without
  a window on a fresh install), and finishes with an import check that prints
  the exact error if a dependency didn't install. A failed `apt-get update`
  no longer aborts the install.

### Fixed

- **Crop Preview showed anamorphic recordings squashed.** UK SD broadcasts are
  stored 720x576 but displayed 16:9; the preview showed the stored pixel grid.
  The frame is now resampled to square pixels using the stream's sample aspect
  ratio, so the picture keeps its on-screen shape. Crop values are unaffected.
- **Cancelling a join mid-render now reports a quiet "Cancelled."** instead of
  an error box, and removes the partial output file.

## [1.5.0] - 2026-07-09

### Added

- **Encoder speed and quality settings** in the output profile editor, for the
  paths that actually re-encode (HEVC output, or cropping). **Encoder speed**
  chooses the x264/x265 preset (Slower … Fastest) and **Quality (CRF)** sets the
  constant rate factor, with an **Automatic** option. Both default to the values
  VRD Next used previously - `faster`, and CRF 24 for HEVC or 20 for H.264 - so
  existing profiles produce identical output. They are disabled for lossless
  profiles, where they have no effect.
- Unusually low (<18) or high (>30) CRF values now ask for confirmation,
  explaining the consequence. Out-of-range values and unknown presets fall back
  to safe defaults, so a hand-edited profile file cannot break an export.

### Changed

- The output profile editor sizes itself to its contents rather than a fixed
  height, so no rows are clipped - including in translations, whose longer text
  needs more room.
- The timeline's cut regions use a slightly lighter red, reading more clearly
  against the green kept scenes.

## [1.4.0] - 2026-07-09

### Added

- **A translatable interface.** Every user-facing string — menus, dialogs,
  buttons, tooltips, messages, the tray Watcher and the stream-info panel — is
  now translatable, with a **Language** setting under **Settings → General**.
  A complete **German** translation is included, along with a German user guide.
  Adding a language needs no code: translate `translations/vrd-next_en.ts`,
  compile it with `translations/compile.sh`, and it appears in the picker.
  Changing language offers to restart the application.
- Qt's own translations are loaded alongside, so standard buttons (OK, Cancel,
  Save) and the file dialogs follow the chosen language too.
- The user guide is shown in the chosen language when a translated copy
  (`assets/help/user-guide_<code>.html`) exists, falling back to English.
- **Batch Manager: remove waiting jobs while the queue is running.** The job
  being processed is protected; everything still waiting can be removed.
- **Batch Manager: a choice when stopping** — finish the current file and then
  stop, or stop straight away.

### Changed

- Theme changes now apply live across the whole interface — chrome, transport
  readouts, icons and buttons — rather than needing a restart.
- Opening a `.vprj` project with *Quick Stream Fix on open* now repairs the
  source first and indexes once, instead of indexing, repairing and re-indexing.
  It's faster, and the project's scene markers map directly onto the repaired
  file rather than being approximated from a frame-count delta.
- **Clear Finished** in the Batch Manager now removes only jobs that completed.
  Cancelled, failed and held jobs stay, matching the behaviour of failed jobs and
  keeping interrupted work resumable.
- The Watcher follows the editor's theme and language.
- The window title no longer repeats the application name.
- The Windows installer targets Python 3.14 and detects a genuine Python
  installation rather than the Microsoft Store's placeholder `python.exe`.
- The Linux installer checks for `ensurepip` and installs the matching
  `python3.X-venv` package, which newer Ubuntu releases require.

### Fixed

- A crash (stack overflow) when switching theme, caused by a palette-change
  recursion in the transport panel.
- The transport panel's readouts kept the old theme's colours after a live theme
  change, because they read their own (stylesheet-resolved) palette.
- Check and radio indicators were nearly invisible on the Light theme.
- The TV renamer no longer misreads an episode whose title looks like an episode
  number — for example a title of "E2" after `S03E21` — as a two-parter.
- The stream-info panel's audio section headings and its "Copy to clipboard"
  output are translated rather than always English.

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

[1.7.0]: https://github.com/infidelus/vrd-next/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/infidelus/vrd-next/compare/v1.5.1...v1.6.0
[1.5.1]: https://github.com/infidelus/vrd-next/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/infidelus/vrd-next/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/infidelus/vrd-next/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/infidelus/vrd-next/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/infidelus/vrd-next/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/infidelus/vrd-next/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/infidelus/vrd-next/releases/tag/v1.0.0
