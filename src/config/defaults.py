DEFAULT_CONFIG = {

    "config_version": 1,

    # Batch Manager: where outputs go, the default output format for new jobs,
    # an optional output-name modifier, and the persisted job queue (so an
    # interrupted overnight batch survives closing the app).
    "batch": {
        "output_folder": "",
        "default_format": "ts",
        "modifier": "",
        "queue": [],
    },

    # Last main-window size, restored on the next launch.
    "window": {
        "width": 1400,
        "height": 900,
        "maximized": False,
    },

    "settings": {

        "qsf_on_open": False,

        "qsf_no_rewarn": False,

        # Delete cached frame indices and the QSF registry entry for a file
        # once it hasn't been used for this many days.  0 = keep forever.
        "cache_max_age_days": 30,

        # Delete remembered renamer matches (the TMDB/IMDb lookups) older than
        # this many days, purged at startup.  0 = keep forever (the default,
        # since renaming is usually one-and-done and the matches are worth
        # keeping; raise it above 0 if you re-process libraries and don't want
        # the cache growing indefinitely).
        "renamer_cache_max_age_days": 0,

        # Delete log files older than this many days at startup.  0 = keep
        # forever.
        "log_max_age_days": 30,

        # Keep at most this many of the most recent log files (per family -
        # editor and watcher are counted separately).  0 = keep every file.
        # Pruned at startup along with the age limit above; either limit can be
        # off independently.
        "log_max_files": 30,

        # Verbose logging: capture smartcut's own (very chatty) output into the
        # log, for deep debugging.  Off by default to keep logs readable.
        "verbose_logging": False,

        # After an audio rebuild, cross-check that the rebuilt sound still lines
        # up with the source at the first cut and log the measured offset.  Adds
        # a couple of seconds to an export (it decodes two short audio windows),
        # so it's off by default; turn it on while testing a new recording to
        # confirm sync without trusting your ears alone.
        "verify_audio_sync": False,

        # Show picture type (I/P/B) on frames: "none", "thumbnails", "preview"
        # or "both".
        "frame_type_display": "none",
        "theme": "system",          # "system" | "light" | "dark"
        "language": "en",           # UI language code; "en" is the built-in default

        # Playback audio volume, 0-100.  Audio plays only during real-time
        # playback (not while frame-stepping or jumping).
        "volume": 80,

        # A/V sync trim, in milliseconds.  This shifts where the SOUND is
        # seeked to, to line it up with the picture.  If the sound runs AHEAD
        # of the video, use a negative value (e.g. -6000 pulls the sound 6s
        # earlier); if it lags behind, use a positive value.  The app reads
        # this once at startup, so close and reopen after editing it.
        "audio_latency_ms": 0,

        # Show hover tooltips on the transport buttons and time boxes.  Handy
        # while you're learning the controls; turn off once they're second
        # nature and you don't want hints popping up.  Read once at startup.
        "show_tooltips": True,

    },

    "paths": {

        # Each "*_mode" is either "last" (remember the last folder used) or
        # "fixed" (always start in the matching "*_folder").  The "last_*"
        # values store the most recently used folder when in "last" mode.

        "open_mode": "last",
        "open_folder": "",
        "last_open": "",

        "export_mode": "last",
        "export_folder": "",
        "last_export": "",

        "project_mode": "last",
        "project_folder": "",
        "last_project": "",

        # Where per-day log files are written.  Empty means use the default
        # location (the app config directory).
        "log_folder": "",

        # Comskip integration: the comskip executable and its .ini config.
        # Empty means "not configured" - the Detect Commercials action will
        # prompt the user to set these in Settings.
        "comskip_binary": "",
        "comskip_ini": "",
        # When set, the Watcher (and manual "Detect Commercials") pick a
        # per-channel Comskip .ini named Comskip_<name>.ini beside comskip_ini,
        # matched against the recording's filename.  Only useful when the
        # recorder writes the channel into the filename (e.g. Tvheadend's $c).
        "comskip_ini_by_channel": False,

        # mkvmerge (mkvtoolnix): used to mux MKV exports so the broadcast LATM
        # AAC is repackaged into native A_AAC (widely compatible) rather than
        # the generic A_MS/ACM wrapper ffmpeg falls back to.  Empty means "auto"
        # - use whatever's found on PATH; set a path here to point at a specific
        # mkvmerge.
        "mkvmerge_binary": "",

        # ffmpeg / ffprobe: the workhorses behind export, join and stream
        # probing.  Empty means "auto" - use whatever's found on PATH; set a
        # path here (Settings > External tools) to point at a specific build.
        # A configured build's folder is prepended to PATH at startup.
        "ffmpeg_binary": "",
        "ffprobe_binary": "",

    },

    "shortcuts": {

        "scene_toggle": "A",

        "detect_commercials": "Ctrl+A",

        "scene_previous": "F5",

        "scene_next": "F6",

        "mark_in": "F3",

        "mark_out": "F4",

        "commit_selection": "Insert",

        "add_unselected": "Ctrl+Insert",

        "clear_all_scenes": "Shift+Insert",

        "goto_selection_start": "S",

        "goto_selection_end": "E",

        "clear_selection": "Delete",

        "undo": "Ctrl+Z",

        "redo": "Ctrl+Y",

        "play_pause": "Space",

        "frame_left": "Left",

        "frame_right": "Right",

        "jump_back_10": "Ctrl+Shift+Left",
        "jump_forward_10": "Ctrl+Shift+Right",

        "jump_back_30": "Ctrl+Left",
        "jump_forward_30": "Ctrl+Right",

        "jump_back_120": "Shift+Left",
        "jump_forward_120": "Shift+Right",

        "jump_start": "Home",

        "jump_end": "End",

        "goto_timecode": "Ctrl+G",

        "show_program_info": "Ctrl+L",

        "open_video": "Ctrl+O",
        "save_video": "Ctrl+S",
        "close_video": "Ctrl+F4",

        "open_project": "Ctrl+Shift+O",
        "save_project": "Ctrl+P",
        "save_project_as": "Ctrl+Shift+P",

        "queue_to_batch": "Ctrl+B",

    }

}