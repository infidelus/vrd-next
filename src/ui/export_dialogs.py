"""
Export dialogs: a live progress dialog and a completion summary, modelled on
VideoReDo's.

ExportProgressDialog shows a smooth percentage bar, the current phase
("Fast Frame Copy" while stream-copying, "Encoding Frames" at cut boundaries),
a scene counter, and an estimated time remaining.  It exposes Pause is omitted
(smartcut has no pause) - only Abort, which cancels the worker.

ExportCompleteDialog shows a VideoReDo-style stats table.
"""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)


_PHASE_LABELS = {
    "copy": "Fast Frame Copy",
    "encode": "Encoding Frames",
    "crop": "Cropping (re-encoding)…",
    "verify": "Verifying output",
    "finalise_mkv": "Finalising MKV…",
    "recode_audio": "Recoding…",
    "recode_full": "Major recode required…",
    "rebuild_audio": "Rebuilding audio…",
    "graft_audio": "Copying audio…",
    "done": "Finishing",
}


def _fmt_secs(secs):
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _fmt_size(num_bytes):
    mb = num_bytes / (1024 * 1024)
    if mb < 1024:
        return f"{mb:.0f} MB"
    return f"{mb / 1024:.2f} GB"


def _fmt_duration(secs):
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_bitrate(bps):
    if not bps:
        return "—"
    mbps = bps / 1_000_000
    if mbps >= 1:
        return f"{mbps:.2f} Mbps"
    return f"{bps / 1000:.0f} kbps"


def _esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class ExportProgressDialog(QDialog):

    def __init__(self, title, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Exporting")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._start_time = None
        self._recode_start = None
        self._aborted = False

        layout = QVBoxLayout(self)

        self._title_label = QLabel(title)
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._eta_label = QLabel("Estimated time remaining: —")
        layout.addWidget(self._eta_label)

        row = QHBoxLayout()
        self._phase_label = QLabel("Preparing…")
        self._scene_label = QLabel("")
        row.addWidget(self._phase_label)
        row.addStretch(1)
        row.addWidget(self._scene_label)
        layout.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setFocusPolicy(Qt.NoFocus)
        self._abort_btn.clicked.connect(self._on_abort)
        btn_row.addWidget(self._abort_btn)
        layout.addLayout(btn_row)

        self._abort_callback = None

    def set_abort_callback(self, cb):
        self._abort_callback = cb

    def _on_abort(self):
        self._aborted = True
        self._abort_btn.setEnabled(False)
        self._abort_btn.setText("Aborting…")
        if self._abort_callback is not None:
            self._abort_callback()

    def update_progress(self, info):
        percent = info.get("percent", 0)
        phase = info.get("phase", "copy")
        scene = info.get("scene", 1)
        total_scenes = info.get("total_scenes", 1)

        # A negative percent means "busy, but with no measurable progress" - the
        # mkvmerge mux and audio verify/rebuild, which can take 20-30s with
        # nothing to count.  Show a pulsing indeterminate bar and a clear phase
        # label so it never looks frozen at 99%.
        if percent < 0:
            self._bar.setRange(0, 0)
            self._phase_label.setText(_PHASE_LABELS.get(phase, "Working…"))
            self._scene_label.setText("")
            self._eta_label.setText("Estimated time remaining: …")
            return

        if self._start_time is None and percent > 0:
            self._start_time = time.perf_counter()

        # The MP4 recode is its own phase with its own progress (driven by
        # ffmpeg) and its own clock, so the bar climbs again and the ETA is
        # measured from when the recode actually started - not from the much
        # faster cut that preceded it.
        recoding = phase in (
            "recode_audio", "recode_full", "rebuild_audio", "crop"
        )

        if self._bar.maximum() == 0:
            self._bar.setRange(0, 100)
        self._bar.setValue(percent)

        if recoding:
            if self._recode_start is None and percent > 0:
                self._recode_start = time.perf_counter()
        else:
            self._recode_start = None

        self._phase_label.setText(_PHASE_LABELS.get(phase, "Working…"))

        if recoding or phase in ("verify", "done"):
            self._scene_label.setText("")
        else:
            self._scene_label.setText(f"Scene {scene} of {total_scenes}")

        # Estimated time remaining.
        if recoding:
            if self._recode_start is not None and 0 < percent < 100:
                elapsed = time.perf_counter() - self._recode_start
                est_total = elapsed / (percent / 100.0)
                remaining = max(0.0, est_total - elapsed)
                self._eta_label.setText(
                    f"Estimated time remaining: {_fmt_secs(remaining)}"
                )
            else:
                self._eta_label.setText("Estimated time remaining: …")
        elif self._start_time is not None and 0 < percent < 100:
            elapsed = time.perf_counter() - self._start_time
            est_total = elapsed / (percent / 100.0)
            remaining = max(0.0, est_total - elapsed)
            self._eta_label.setText(
                f"Estimated time remaining: {_fmt_secs(remaining)}"
            )
        elif percent >= 100:
            self._eta_label.setText("Estimated time remaining: done")


class ExportCompleteDialog(QDialog):

    def __init__(self, stats, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Output Processing Complete")
        self.setModal(True)
        self.setMinimumWidth(340)

        self._out_path = stats.get("out_path", "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        out_path = self._out_path
        filename = out_path.rsplit("/", 1)[-1] if out_path else ""

        rows = [
            ("Video length:", _fmt_duration(stats.get("duration_secs", 0))),
            ("Video size:", _fmt_size(stats.get("out_size", 0))),
            ("Output scenes:", str(stats.get("scenes", 0))),
            ("Video output frames:", f"{stats.get('video_frames', 0):,}"),
            ("Audio output frames:", f"{stats.get('audio_frames', 0):,}"),
            ("Audio tracks:", str(stats.get("audio_tracks", 0))),
            ("Processing time:", _fmt_secs(stats.get("processing_secs", 0))),
            ("Processed frames/sec:", f"{stats.get('fps', 0):.0f}"),
            ("Video bitrate:", _fmt_bitrate(stats.get("video_bitrate", 0))),
        ]

        # Build alternating-row HTML table (VideoReDo-style).
        row_html = []
        for i, (label, value) in enumerate(rows):
            bg = "#2f3136" if i % 2 == 0 else "#26282c"
            row_html.append(
                f'<tr style="background:{bg};">'
                f'<td style="padding:8px 18px; color:#9aa0a6; '
                f'white-space:nowrap;">{_esc(label)}</td>'
                f'<td style="padding:8px 18px; color:#e8eaed; '
                f'font-weight:600;" align="right">{_esc(value)}</td>'
                f'</tr>'
            )

        errors = stats.get("errors", [])
        notes = stats.get("notes", [])

        # Footnotes continue the figures' alternating-row pattern so they sit
        # flush with the rows above (VRD-style asterisks); detail is in the log.
        for j, note in enumerate(notes):
            bg = "#2f3136" if (len(rows) + j) % 2 == 0 else "#26282c"
            row_html.append(
                f'<tr style="background:{bg};">'
                f'<td colspan="2" style="padding:8px 18px; color:#9aa0a6;">'
                f'* {_esc(note)}</td></tr>'
            )

        # Top status line: blank on a clean export; genuine problems shown in a
        # warning colour so they're noticed.  Full detail is in the log.
        status_row = ""
        if errors:
            status_row = (
                '<tr style="background:#3a3d42;">'
                '<td style="padding:0 18px 18px 18px; color:#e0915f; '
                'font-size:12px;">'
                + "<br>".join(_esc(e) for e in errors)
                + '</td></tr>'
            )
        header_pad_bottom = "8px" if errors else "18px"

        # The header uses the same single-column table + 18px cell padding as
        # the rows below, so the filename and status line up with the labels.
        html = f"""
        <table cellspacing="0" cellpadding="0" width="100%">
          <tr style="background:#3a3d42;">
            <td style="padding:18px 18px {header_pad_bottom} 18px;
                       font-size:15px; font-weight:700; color:#f1f3f4;
                       line-height:150%;">
              {_esc(filename)}
            </td>
          </tr>
          {status_row}
        </table>
        <table cellspacing="0" cellpadding="0" width="100%"
               style="font-size:12px;">
          {''.join(row_html)}
        </table>
        """

        body = QLabel(html)
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setAlignment(Qt.AlignTop)
        # Constrain the width so long filenames wrap instead of stretching the
        # dialog wide.  This keeps the dialog compact and tall like VideoReDo's.
        body.setMaximumWidth(360)
        layout.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(14, 12, 14, 12)

        open_btn = QPushButton("Open Folder")
        open_btn.setFocusPolicy(Qt.NoFocus)
        open_btn.clicked.connect(self._open_folder)
        btn_row.addWidget(open_btn)

        btn_row.addStretch(1)

        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.setFocusPolicy(Qt.NoFocus)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(ok)

        layout.addLayout(btn_row)

        self.setMaximumWidth(380)

    def _open_folder(self):
        import os
        from utils.open_path import open_path

        folder = os.path.dirname(self._out_path)
        if folder and os.path.isdir(folder):
            open_path(folder)
