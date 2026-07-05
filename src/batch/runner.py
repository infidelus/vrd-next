"""Sequential processing of a batch of saved projects.

`process_job` does the work for one job and is Qt-free so it can be unit-tested
headlessly; `BatchRunner` is the QThread that walks the queue and turns each
step into Qt signals for the Batch Manager window.

Repair policy: the batch never silently Quick-Stream-Fixes and exports a file.
A QSF can shift cut points slightly (e.g. a few stray frames at the tail), so a
file that can't be exported as-is is left in a "needs review" state for the user
to open, repair and confirm the cuts via the Batch Manager's Edit button - it is
never cut unattended.
"""

import logging
import os

from PySide6.QtCore import QThread, Signal

from batch.job import (
    QUEUED, RUNNING, DONE, FAILED, CANCELLED, NEEDS_REVIEW,
    build_dest_path, container_to_fmt,
)
from addons.output_profiles import resolve_profile
from export.exporter import export_ranges, ExportError
from media.frame_index import build_index_sync
from project.vprj import read_source_filename, load_vprj
from utils.winepath import resolve_source

log = logging.getLogger("vrd-next.batch")


class JobError(Exception):
    """A job couldn't be completed; the message is shown against the job."""


class NeedsReview(Exception):
    """A job can't be exported as-is and needs the user to repair/confirm it
    (it is held rather than failed)."""


def _delete_quietly(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def process_job(
        job,
        out_folder,
        modifier,
        config,
        taken=None,
        progress_cb=None,
        cancel_cb=None,
):
    """Process one BatchJob.  Sets job.source_path / job.dest_path and returns
    the export stats dict on success.

    Raises NeedsReview if the file needs repair before it can be cut (held, not
    failed), or JobError on any genuine failure.

    `config` supplies the output profiles, resolved per job by name.  `taken` is
    a set of output paths already claimed by earlier jobs in this batch, so two
    jobs never collide on the same name.
    """
    taken = taken if taken is not None else set()
    cancel_cb = cancel_cb or (lambda: False)

    # --- locate the recording the project refers to ----------------------- #
    embedded = read_source_filename(job.vprj_path)
    if not embedded:
        raise JobError("The project file has no source recording recorded.")

    source = resolve_source(embedded)
    if source is None:
        raise JobError(f"Source recording not found:\n{embedded}")
    job.source_path = source

    try:
        stats = _index_and_export(
            job, source, out_folder, modifier, config,
            taken, progress_cb, cancel_cb,
        )
    except ExportError as exc:
        # Clean up the unusable output the failed export may have produced.
        _delete_quietly(job.dest_path)
        msg = str(exc)
        if "no readable video" in msg.lower():
            # Needs a Quick Stream Fix first - hold it for the user to repair
            # and confirm the cuts, rather than cutting it unattended.
            raise NeedsReview(
                "Needs repair before cutting - open Edit to run Quick Stream "
                "Fix and confirm the cuts, then it will process on the next run."
            )
        raise JobError(msg)

    # The export "succeeded", but if the audio still couldn't be made readable
    # (the rebuild from source failed for some reason), the file genuinely
    # needs a Quick Stream Fix - hold it for review rather than ship a file
    # with broken audio.  Normally the in-sync rebuild handles it and this is
    # not hit.
    if isinstance(stats, dict) and stats.get("audio_needs_repair"):
        _delete_quietly(job.dest_path)
        raise NeedsReview(
            "Needs repair before cutting (its audio couldn't be rebuilt) - "
            "open Edit to run Quick Stream Fix and confirm the cuts, then it "
            "will process on the next run."
        )
    return stats


def _index_and_export(
        job, export_source, out_folder, modifier, config,
        taken, progress_cb, cancel_cb,
):
    """Index `export_source`, map the project's cuts onto it, and export it
    through the job's output profile (container, audio handling, aspect)."""
    if progress_cb:
        progress_cb({"phase": "index", "percent": 0})

    index = build_index_sync(export_source)

    data = load_vprj(job.vprj_path, index)
    keep_ranges = list(data.keep_ranges)
    if not keep_ranges:
        raise JobError("The project has no kept segments to export.")

    if cancel_cb():
        raise JobError("Cancelled.")

    # The profile drives the container, audio and aspect.  Resolve by name so a
    # profile edited or deleted after queueing still falls back to something
    # usable rather than orphaning the job.
    profile = resolve_profile(config, job.profile_name)

    dest = build_dest_path(
        out_folder, modifier, job.source_path,
        container_to_fmt(profile.container), taken,
    )
    job.dest_path = dest
    taken.add(dest)

    os.makedirs(out_folder, exist_ok=True)

    return export_ranges(
        export_source,
        dest,
        keep_ranges,
        index,
        out_format=profile.container,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
        rebuild_audio=True,
        audio_mode=profile.audio,
        audio_bitrate=profile.audio_bitrate,
        aspect=profile.aspect,
        crop_mode=getattr(profile, "crop_mode", "none"),
        crop=getattr(profile, "crop", (0, 0, 0, 0)),
        video_mode=getattr(profile, "video", "copy"),
    )


# Statuses the runner re-attempts when Start is pressed.  DONE is skipped
# (already exported) and NEEDS_REVIEW is skipped (held until the user releases
# it via Edit).
_SKIP_STATUSES = (DONE, NEEDS_REVIEW)


class BatchRunner(QThread):
    """Walks the job list, processing one job at a time."""

    job_started = Signal(int)               # (index)
    job_progress = Signal(int, dict)        # (index, info)
    job_done = Signal(int, dict)            # (index, stats)
    job_failed = Signal(int, str)           # (index, message)
    job_held = Signal(int, str)             # (index, reason) - needs review
    # batch_finished(completed, failed, held, cancelled)
    batch_finished = Signal(int, int, int, bool)

    def __init__(self, jobs, out_folder, modifier, config, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._out_folder = out_folder
        self._modifier = modifier
        self._config = config
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        taken = set()
        completed = 0
        failed = 0
        held = 0
        cancelled = False

        # Walk by index so jobs appended while running (queued on the fly) are
        # picked up rather than missed.
        i = 0
        while True:
            if self._stop:
                cancelled = True
                break
            if i >= len(self._jobs):
                break
            job = self._jobs[i]
            i += 1

            if job.status in _SKIP_STATUSES:
                if job.status == DONE:
                    completed += 1
                elif job.status == NEEDS_REVIEW:
                    held += 1
                continue

            job.status = RUNNING
            job.percent = 0
            self.job_started.emit(i - 1)

            def _progress(info, _i=i - 1, _job=job):
                pct = info.get("percent")
                if isinstance(pct, (int, float)):
                    _job.percent = int(pct)
                self.job_progress.emit(_i, info)

            try:
                stats = process_job(
                    job,
                    self._out_folder,
                    self._modifier,
                    self._config,
                    taken=taken,
                    progress_cb=_progress,
                    cancel_cb=lambda: self._stop,
                )
                if self._stop:
                    job.status = CANCELLED
                    cancelled = True
                    break
                job.status = DONE
                job.percent = 100
                completed += 1
                self.job_done.emit(i - 1, stats if isinstance(stats, dict) else {})
            except NeedsReview as exc:
                if self._stop:
                    job.status = CANCELLED
                    cancelled = True
                    break
                job.status = NEEDS_REVIEW
                job.message = str(exc)
                held += 1
                log.info("Batch job held for review: %s", job.name)
                self.job_held.emit(i - 1, str(exc))
            except JobError as exc:
                if self._stop:
                    job.status = CANCELLED
                    cancelled = True
                    break
                job.status = FAILED
                job.message = str(exc)
                failed += 1
                log.warning("Batch job failed: %s - %s", job.name, exc)
                self.job_failed.emit(i - 1, str(exc))
            except Exception as exc:    # never let one job kill the batch
                job.status = FAILED
                job.message = str(exc)
                failed += 1
                log.exception("Batch job crashed: %s", job.name)
                self.job_failed.emit(i - 1, str(exc))

        self.batch_finished.emit(completed, failed, held, cancelled)
