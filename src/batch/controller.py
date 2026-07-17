"""Owns the batch queue, its settings and the running worker.

The controller lives on the main window, not on the Batch Manager dialog, so a
batch keeps running in the background when the dialog is closed - and so the
queue (and each job's outcome) survives both closing the dialog and restarting
the app.  The dialog is just a view: it reads the controller's jobs and reacts
to its signals.
"""

import os

from PySide6.QtCore import QObject, Signal

from batch.job import BatchJob, QUEUED, RUNNING, DONE, NEEDS_REVIEW
from batch.runner import BatchRunner
from addons.output_profiles import default_profile_name


class BatchController(QObject):

    # The job list changed (added / removed / reordered / cleared).
    jobs_changed = Signal()
    # Per-job updates, by row index.
    job_started = Signal(int)
    job_progress = Signal(int, dict)
    job_done = Signal(int, dict)
    job_failed = Signal(int, str)
    job_held = Signal(int, str)
    # batch_finished(completed, failed, held, cancelled)
    batch_finished = Signal(int, int, int, bool)
    # Whether a batch is currently running.
    running_changed = Signal(bool)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.jobs = []
        self.runner = None
        self._load_queue()

    # ------------------------------------------------------------------ #
    # Settings (persisted in config["batch"])
    # ------------------------------------------------------------------ #

    def _cfg(self):
        return self.config.setdefault("batch", {})

    @property
    def out_folder(self):
        return self._cfg().get("output_folder") or os.path.join(
            os.path.expanduser("~"), "Videos"
        )

    @out_folder.setter
    def out_folder(self, value):
        self._cfg()["output_folder"] = value
        self._persist()

    @property
    def default_profile(self):
        """Name of the profile a freshly-queued job starts on.  Defaults to a
        favourite (or the first profile) until the user picks one here."""
        return self._cfg().get("default_profile") or default_profile_name(
            self.config
        )

    @default_profile.setter
    def default_profile(self, value):
        self._cfg()["default_profile"] = value
        self._persist()

    @property
    def modifier(self):
        return self._cfg().get("modifier", "")

    @modifier.setter
    def modifier(self, value):
        self._cfg()["modifier"] = value
        self._persist()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _persist(self):
        try:
            from config.loader import save_config
            save_config(self.config)
        except Exception:
            pass

    def _load_queue(self):
        # The queue lives in its own file (queue.json) rather than in
        # settings.json - it's transient state, and keeping it separate means a
        # problem with one file can't take the other down.  Migrate any queue
        # an older version stored inline under config["batch"]["queue"].
        from config.loader import load_sidecar, save_sidecar
        entries = load_sidecar("queue.json", default=None)
        if entries is None:
            legacy = self.config.get("batch", {}).get("queue")
            if legacy:
                entries = legacy
                save_sidecar("queue.json", entries)
                self.config.get("batch", {}).pop("queue", None)
                self._persist()          # drop the old key from settings.json
            else:
                entries = []
        for data in entries:
            self.jobs.append(BatchJob.from_dict(data))
        # Drop entries whose files are gone - but only finished ones.  A DONE
        # job is just a record; if its source or output has since been deleted
        # there's nothing to keep.  A QUEUED job is left alone even if its file
        # isn't reachable right now, because that's often a temporarily
        # unmounted drive (an NFS/SMB share), not a real deletion - purging it
        # would silently lose pending work the moment a network share was down
        # at startup.
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if self._job_worth_keeping(j)]
        if len(self.jobs) != before:
            self.save_queue()            # persist the pruned list immediately

    @staticmethod
    def _job_worth_keeping(job):
        """Whether a loaded job should stay in the queue.  Finished jobs are
        dropped once their referenced files no longer exist; unfinished jobs
        are always kept (a missing file may just be an unmounted drive)."""
        if job.status != DONE:
            return True
        # A finished job: keep it only while something it refers to still
        # exists - the output if we recorded one, otherwise the source project.
        ref = job.dest_path or job.vprj_path
        return bool(ref and os.path.exists(ref))

    def save_queue(self):
        from config.loader import save_sidecar
        save_sidecar("queue.json", [j.to_dict() for j in self.jobs])

    def persist_now(self):
        """Write the queue to disk immediately, callable from the runner
        thread the instant a job reaches a terminal status.

        The dialog's normal save happens via the job_done/job_failed signals,
        which are delivered on the main thread by the event loop - but if the
        app or Batch Manager is torn down before that delivery drains (for
        example the user stops "after current job" and closes straight away),
        the finished status would never reach disk and the job would come back
        as queued on next launch.  Writing here, synchronously, closes that
        window.  save_config already serialises to a temp file and renames, so
        a concurrent main-thread save can't corrupt it - last write wins, and
        both write the same DONE state.
        """
        self.save_queue()

    # ------------------------------------------------------------------ #
    # Queue editing
    # ------------------------------------------------------------------ #

    def add_job(self, vprj_path, profile_name=None):
        self.jobs.append(BatchJob(vprj_path, profile_name or self.default_profile))
        self.save_queue()
        self.jobs_changed.emit()

    def add_jobs(self, paths):
        for p in paths:
            self.jobs.append(BatchJob(p, self.default_profile))
        if paths:
            self.save_queue()
            self.jobs_changed.emit()

    def running_row(self):
        """The row currently being processed, or -1 if none.  Used to protect
        the active job from being removed while the queue runs."""
        if self.runner is None:
            return -1
        return getattr(self.runner, "current_index", -1)

    def remove(self, rows):
        """Remove the given rows.  The job that's currently being processed is
        never removed - stop the batch first - but anything waiting can go, even
        while the queue is running."""
        active = self.running_row()
        rows = [r for r in rows if r != active]
        if not rows:
            return
        for r in sorted(rows, reverse=True):
            if 0 <= r < len(self.jobs):
                del self.jobs[r]
        # The runner walks this same list by index, so keep its cursor honest.
        if self.runner is not None:
            self.runner.note_removed(rows)
        self.save_queue()
        self.jobs_changed.emit()

    def move(self, row, delta):
        nr = row + delta
        if 0 <= row < len(self.jobs) and 0 <= nr < len(self.jobs):
            # Never move the running job, and never swap another job past it -
            # the UI gates this too, but guard here as the source of truth.
            if self.jobs[row].status == RUNNING or \
                    self.jobs[nr].status == RUNNING:
                return row
            self.jobs[row], self.jobs[nr] = self.jobs[nr], self.jobs[row]
            self.save_queue()
            self.jobs_changed.emit()
            return nr
        return row

    def clear_finished(self):
        """Remove the jobs that actually finished.

        Only DONE jobs go.  A cancelled job never finished - it was interrupted
        and produced no usable output, and pressing Start again picks it up
        where it left off - so it stays, as do failed jobs and ones held for
        review.  Anything unwanted can still be removed by hand.

        Safe to call while the batch is running: DONE jobs are inert, and this
        goes through the same cursor-aware removal as remove(), so the runner's
        position stays correct and the job being processed is untouched.
        """
        done_rows = [i for i, j in enumerate(self.jobs) if j.status == DONE]
        if not done_rows:
            return
        for r in sorted(done_rows, reverse=True):
            del self.jobs[r]
        if self.runner is not None:
            self.runner.note_removed(done_rows)
        self.save_queue()
        self.jobs_changed.emit()

    def set_job_profile(self, row, name):
        if 0 <= row < len(self.jobs):
            self.jobs[row].profile_name = name
            self.save_queue()

    # ------------------------------------------------------------------ #
    # Running
    # ------------------------------------------------------------------ #

    def is_running(self):
        return self.runner is not None

    def is_finishing(self):
        """True when a stop-after-current-job is pending: the batch is still
        running but will halt once the job in progress completes.  Lets the
        dialog restore the "stopping after the current file" message when it's
        reopened, instead of reverting to plain "batch running"."""
        return (self.runner is not None
                and getattr(self.runner, "_finish_current", False))

    def pending_count(self):
        # Jobs that Start would actually process: not already done, and not
        # held for review (those wait for the user to release them via Edit).
        return sum(
            1 for j in self.jobs if j.status not in (DONE, NEEDS_REVIEW)
        )

    def held_count(self):
        return sum(1 for j in self.jobs if j.status == NEEDS_REVIEW)

    def requeue(self, row):
        """Release a held (needs-review) job back to the queue so the next run
        will retry it - used when the user opens it via Edit to confirm."""
        if 0 <= row < len(self.jobs) and self.jobs[row].status == NEEDS_REVIEW:
            self.jobs[row].status = QUEUED
            self.jobs[row].message = ""
            self.save_queue()
            self.jobs_changed.emit()

    def start(self):
        if self.runner is not None:
            return
        self.runner = BatchRunner(
            self.jobs, self.out_folder, self.modifier, self.config, self
        )
        self.runner.job_started.connect(self.job_started)
        self.runner.job_progress.connect(self.job_progress)
        self.runner.job_done.connect(self._on_job_done)
        self.runner.job_failed.connect(self._on_job_failed)
        self.runner.job_held.connect(self._on_job_held)
        self.runner.batch_finished.connect(self._on_batch_finished)
        self.running_changed.emit(True)
        self.runner.start()

    def stop(self, after_current=False):
        if self.runner is not None:
            self.runner.stop(after_current=after_current)

    def wait(self, ms=5000):
        if self.runner is not None:
            self.runner.wait(ms)

    # Runner signal handlers - persist as we go, then re-emit for the dialog.
    def _on_job_done(self, index, stats):
        self.save_queue()
        self.job_done.emit(index, stats)

    def _on_job_failed(self, index, message):
        self.save_queue()
        self.job_failed.emit(index, message)

    def _on_job_held(self, index, reason):
        self.save_queue()
        self.job_held.emit(index, reason)

    def _on_batch_finished(self, completed, failed, held, cancelled):
        self.runner = None
        self.save_queue()
        self.running_changed.emit(False)
        self.batch_finished.emit(completed, failed, held, cancelled)
