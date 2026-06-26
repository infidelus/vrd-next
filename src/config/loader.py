import json

from pathlib import Path

from config.defaults import (
    DEFAULT_CONFIG,
)


CONFIG_DIR = (

    Path.home()

    /

    ".config"

    /

    "vrd-next"

)

CONFIG_FILE = (

    CONFIG_DIR

    /

    "config.json"

)


def ensure_config():

    CONFIG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not CONFIG_FILE.exists():

        CONFIG_FILE.write_text(

            json.dumps(

                DEFAULT_CONFIG,

                indent=4,

            )

        )

    with open(
            CONFIG_FILE,
            encoding="utf-8",
    ) as f:
        config = json.load(
            f
        )

    #
    # Merge in any keys added in newer versions that an older config file on
    # disk won't have (e.g. the settings section).
    #

    changed = False

    for key, value in DEFAULT_CONFIG.items():
        if key not in config:
            config[key] = value
            changed = True
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key not in config[key]:
                    config[key][sub_key] = sub_value
                    changed = True

    # One-time migrations, gated on a stored schema version so each runs at
    # most once.  This is what lets a user later choose any of these bindings
    # for themselves (e.g. put Add Selection back on Return, or Mark In on I)
    # without the next load reverting it.  A missing version means "never
    # migrated" (a pre-versioning config); a config built fresh from the
    # defaults already holds the new values, so the moves below find nothing to
    # do.
    CONFIG_VERSION = 2
    version = config.get("config_version", 0)
    sc = config.get("shortcuts")

    if isinstance(sc, dict):
        if version < 1:
            # save_project was split into save_project (Ctrl+P, overwrite) and
            # save_project_as (Ctrl+Shift+P); move older configs off the old
            # combined binding so save_project_as can take Ctrl+Shift+P.
            if sc.get("save_project") == "Ctrl+Shift+P":
                sc["save_project"] = "Ctrl+P"
                changed = True

        if version < 2:
            # Selection shortcuts moved to a VRD-style scheme - Mark In/Out to
            # F3/F4 and Add Selection to Insert (freeing Return).  Only a config
            # still on the previous defaults is moved; a customised binding is
            # left alone.
            for action, (old, new) in (
                ("mark_in", ("I", "F3")),
                ("mark_out", ("O", "F4")),
                ("commit_selection", ("Return", "Insert")),
            ):
                if sc.get(action) == old:
                    sc[action] = new
                    changed = True

    if version < CONFIG_VERSION:
        config["config_version"] = CONFIG_VERSION
        changed = True

    if changed:
        save_config(config)

    return config


def save_config(config):
    CONFIG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    CONFIG_FILE.write_text(
        json.dumps(
            config,
            indent=4,
        )
    )