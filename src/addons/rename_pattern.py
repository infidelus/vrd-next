"""Turn episode / movie metadata into a new filename via a small pattern.

A pragmatic subset of the Rename-My-TV-Series codes - enough for tidy
Plex / Jellyfin / Kodi naming, with room to grow:

    %N        series / movie name
    %NY       name with year, e.g. 'Castle (2009)'
    %Y        year (show's first-air year, or movie release year)
    %S        season number
    %SZ       season number, zero-padded to two digits
    %E        episode number
    %EZ       episode number, zero-padded to two digits
    %T        episode title (blank for movies)
    %TMDBID   the TMDB id

Multi-episode files expand the episode codes across every episode joined with a
dash, so for episodes 11 and 12 'S%SZE%EZ' becomes 'S05E11-12'.

A pattern may contain '/' separators to build a folder tree as well as a file
name, the way FileBot and tinyMediaManager do - e.g.

    %NY/Season %S/%N - S%SZE%EZ - %T
      -> Castle (2009)/Season 2/Castle - S02E01 - Deep in Death

The folder padding is therefore just another code: an unpadded 'Season %S'
folder can sit above padded 'S%SZE%EZ' files.  Only the pattern's own '/'
divide folders - any '/' that turns up inside a substituted value (a title like
'9/11') is turned into a space first, so a stray title can never spawn an extra
folder.  Each path segment is sanitised on its own to stay safe across the
filesystems media is commonly served from.  The extension is not handled here -
the caller re-attaches the original one.
"""

import re

# Sensible defaults: the Plex/Jellyfin/Kodi tree Sean uses, with an unpadded
# season folder over padded episode files.
DEFAULT_TV_PATTERN = "%NY/Season %S/%N - S%SZE%EZ - %T"
DEFAULT_MOVIE_PATTERN = "%N (%Y)/%N (%Y)"

# Characters that are illegal or troublesome across common media filesystems.
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')

# Path separators to neutralise inside a substituted value, so only the
# pattern's literal '/' separate folders.
_PATHSEP = re.compile(r"[\\/]")

# Codes, longest first so e.g. %TMDBID wins over %T and %SZ over %S.
_CODE = re.compile(r"%(TMDBID|NY|SZ|EZ|N|Y|S|E|T)")


def sanitise(name):
    """Make a string safe to use as a single filename / folder-name segment.

    Illegal characters become spaces rather than vanishing, so titles like
    'Part 1/2' read as 'Part 1 2' instead of mashing into 'Part 12'.
    """
    name = _ILLEGAL.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.rstrip(". ")          # no trailing dot/space (Windows shares)


def _episodes(meta):
    eps = meta.get("episodes")
    if not eps and meta.get("episode") is not None:
        eps = [meta["episode"]]
    return eps or []


def _render(pattern, meta):
    """Substitute the codes in ``pattern`` into raw text.

    Each substituted *value* has its own path separators turned into spaces, so
    that when the result is later split into folders only the pattern's literal
    '/' count.  No whole-string sanitising happens here - that is done per
    segment by the callers, after any folder split.
    """
    name = meta.get("name") or ""
    year = meta.get("year")
    season = meta.get("season")
    title = meta.get("title") or ""
    tmdb_id = meta.get("tmdb_id")
    episodes = _episodes(meta)

    def ep_run(width):
        if not episodes:
            return ""
        return "-".join(
            ("%0*d" % (width, e)) if width else str(e) for e in episodes
        )

    def repl(match):
        code = match.group(1)
        if code == "N":
            val = name
        elif code == "NY":
            val = "%s (%d)" % (name, year) if year else name
        elif code == "Y":
            val = str(year) if year else ""
        elif code == "S":
            val = str(season) if season is not None else ""
        elif code == "SZ":
            val = "%02d" % season if season is not None else ""
        elif code == "E":
            val = ep_run(0)
        elif code == "EZ":
            val = ep_run(2)
        elif code == "T":
            val = title
        elif code == "TMDBID":
            val = str(tmdb_id) if tmdb_id else ""
        else:
            val = match.group(0)
        # Keep a value's own slashes from spawning folders.
        return _PATHSEP.sub(" ", val)

    return _CODE.sub(repl, pattern)


def format_name(pattern, meta):
    """Render ``pattern`` into a single sanitised base name (no folders).

    Any '/' in the pattern is flattened to a space.  Kept for callers that only
    ever want a flat file name; most now use :func:`format_path`.
    """
    return sanitise(_render(pattern, meta).replace("/", " "))


def format_path(pattern, meta):
    """Render ``pattern`` into a sanitised *relative path*.

    '/' in the pattern divide folder segments; the final segment is the file's
    base name (the caller re-attaches the extension).  Each segment is sanitised
    on its own and empty segments - a code that rendered blank - are dropped, so
    a missing year never leaves a stray empty folder.  Returns a '/'-joined
    string, or '' if nothing rendered.

    Specials (season 0) get the conventional folder name ``Specials`` instead of
    ``Season 0``: every common media server (Plex, Jellyfin, Emby, Kodi)
    recognises it, and it reads better in a file browser.  Only the season
    *folder* is renamed - the file keeps its ``S00Exx`` numbering, which is what
    the scrapers actually match on.
    """
    rendered = _render(pattern, meta)
    # The pattern's literal '/' are the only separators left in ``rendered``
    # (each value had its own slashes neutralised), so the rendered segments line
    # up one-for-one with the pattern's own segments.
    pattern_segments = pattern.split("/")
    rendered_segments = rendered.split("/")
    last = len(rendered_segments) - 1
    specials = _is_specials(meta.get("season"))

    parts = []
    for i, seg in enumerate(rendered_segments):
        is_folder = i < last
        uses_season = i < len(pattern_segments) and "%S" in pattern_segments[i]
        if specials and is_folder and uses_season:
            parts.append("Specials")          # the season-0 folder, by convention
        else:
            parts.append(sanitise(seg))
    parts = [p for p in parts if p]
    return "/".join(parts)


def _is_specials(season):
    """True when ``season`` represents season 0 (a special)."""
    try:
        return season is not None and int(season) == 0
    except (TypeError, ValueError):
        return False
