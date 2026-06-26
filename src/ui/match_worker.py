"""Run TMDB matching off the UI thread, with a cancellable progress dialog.

A big mixed folder can mean dozens of TMDB lookups, which would otherwise sit on
a wait cursor with the window frozen.  ``MatchWorker`` runs the blocking match
function on a background thread; ``MatchRunner`` wires it to a ``QProgressDialog``
so the user sees progress and can cancel.

The match function is called as ``fn(progress_cb)``.  ``progress_cb(done, total,
label)`` reports progress and returns ``False`` once the user has cancelled, at
which point the match function should stop as soon as it can (the partial result
is kept).
"""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QProgressDialog


class MatchWorker(QThread):
    progress = Signal(int, int, str)   # done, total, label
    finished_ok = Signal(str)          # "" on success/cancel, else an error

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        def progress_cb(done, total, label=""):
            self.progress.emit(int(done), int(total), str(label))
            return not self._cancelled

        try:
            self._fn(progress_cb)
            self.finished_ok.emit("")
        except Exception as exc:        # surface any failure to the UI thread
            self.finished_ok.emit(str(exc) or exc.__class__.__name__)


class MatchRunner:
    """Owns one match run's progress dialog and worker, keeping them alive
    until it finishes and then calling ``on_finished(error)`` on the UI thread.
    """

    def __init__(self, parent, title, on_finished, verb="Matching",
                 show_count=True):
        self._on_finished = on_finished
        self._verb = verb
        # When True the label appends "(done+1 of total)" - right for counting
        # items (matching).  Movers report bytes as permille and put their own
        # "(N of M)" file count in the label, so they pass False.
        self._show_count = show_count
        self._dialog = QProgressDialog(title, "Cancel", 0, 0, parent)
        self._dialog.setWindowModality(Qt.WindowModal)
        # Pin the width so a long label (e.g. the mover's "Renaming: old → new")
        # can't balloon the dialog - QProgressDialog otherwise grows to fit its
        # longest-ever label and never shrinks back.  Labels are elided to fit
        # in _on_progress.
        width = 460
        if parent is not None and parent.width() > 0:
            width = min(max(420, int(parent.width() * 0.7)), 620)
        self._dialog.setFixedWidth(width)
        # Don't flash a dialog for quick jobs; only show once it's clearly
        # taking a moment.  Qt manages showing it from setValue().
        self._dialog.setMinimumDuration(300)
        self._dialog.setAutoClose(False)
        self._dialog.setAutoReset(False)
        self._worker = None

    def start(self, fn):
        self._worker = MatchWorker(fn, self._dialog.parent())
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._dialog.canceled.connect(self._worker.cancel)
        self._worker.start()

    def _on_progress(self, done, total, label):
        if total > 0:
            self._dialog.setRange(0, total)
            self._dialog.setValue(min(done, total))
        if label:
            if not self._verb:
                # The caller's label is already the full message (e.g. the
                # mover's "Renaming: …" / "Moving: …").
                text = label
            elif self._show_count:
                text = ("%s: %s  (%d of %d)"
                        % (self._verb, label, min(done + 1, total), total))
            else:
                text = "%s: %s" % (self._verb, label)
        else:
            text = "Finishing…"
        self._dialog.setLabelText(self._elide(text))

    def _elide(self, text):
        """Trim ``text`` to the dialog's pinned width, keeping the start (the
        action) and the end (the "(N of M)" count) and eliding the middle, so a
        long file name can't widen the dialog."""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._dialog.font())
        avail = max(80, self._dialog.width() - 48)     # leave room for margins
        return fm.elidedText(text, Qt.ElideMiddle, avail)

    def _on_done(self, error):
        self._dialog.reset()
        self._dialog.close()
        if self._worker is not None:
            self._worker.wait()
        self._on_finished(error)
