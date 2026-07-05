"""Pull a show name, season and episode out of a recording's filename.

Targets the common 'SxxExx' convention in any case, including multi-episode
files written 'SxxExxExx' or 'SxxExx-Exx'.  The 'NxEE' style (e.g. '1x02',
'01x02', and multi-parters '1x02-03' / '1x02x03') is also recognised.  The show
name is whatever sits before the season/episode tag, with separators (dots,
underscores, dashes) tidied into spaces.  A four-digit year in the name (e.g.
'Castle 2009') is pulled out separately so it can sharpen the metadata search.

No Qt, no third-party dependencies - just text in, structured data out, so it
can be unit-tested in isolation.
"""

import os
import re

# S05E11 / s05e11 / S05.E11, optionally followed by further episodes that must
# each carry their own 'E' (E12, -E12, E12E13).  Requiring the 'E' on extras
# stops quality tags like '1080' being mistaken for an episode number, and the
# extra separator excludes whitespace so a Plex-style title that follows a
# " - " (e.g. "S03E21 - E2", where "E2" is the episode title) isn't swallowed
# as a second episode.
_SXXEXX = re.compile(
    r"[Ss](?P<season>\d{1,2})[\s._-]*[Ee](?P<ep>\d{1,3})"
    r"(?P<extra>(?:[._-]*[Ee]\d{1,3})+)?"
)

# 1x02 / 01x02 / 12x05, with optional multi-parters 1x02-03 or 1x02x03.  The
# leading boundary (not preceded by a letter or digit) keeps resolutions such as
# '1920x1080' from being read as a season/episode.
_NXEE = re.compile(
    r"(?<![A-Za-z0-9])(?P<season>\d{1,2})[xX](?P<ep>\d{1,3})"
    r"(?P<extra>(?:[xX-]\d{1,3})+)?"
)

# A standalone year, bounded so it isn't picked out of a larger number.
_YEAR = re.compile(
    r"(?:^|[\s._\-(\[])(?P<year>19\d{2}|20\d{2})(?:[\s._\-)\]]|$)"
)


class ParsedName:
    """The pieces teased out of one filename."""

    def __init__(self, show, season, episodes, year, ext, matched):
        self.show = show              # cleaned show-name guess
        self.season = season          # int or None
        self.episodes = episodes      # list of ints, e.g. [11] or [11, 12]
        self.year = year              # int or None
        self.ext = ext                # extension without the dot, e.g. 'ts'
        self.matched = matched        # True if a season/episode tag was found

    @property
    def episode(self):
        return self.episodes[0] if self.episodes else None

    def __repr__(self):
        return (
            "ParsedName(show=%r, season=%r, episodes=%r, year=%r, ext=%r, "
            "matched=%r)"
            % (self.show, self.season, self.episodes, self.year, self.ext,
               self.matched)
        )


def _clean(text):
    """Tidy a raw name fragment into a human-readable show name."""
    text = re.sub(r"[._]+", " ", text)        # dots/underscores -> spaces
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def _split_year(fragment):
    """Return (name_without_year, year_or_None) for a leading-ish year."""
    m = _YEAR.search(fragment)
    if not m:
        return fragment, None
    year = int(m.group("year"))
    # Cut the name before the year, but only if there is a name in front of it
    # (a filename that starts with a year keeps its text).
    if m.start() > 0:
        return fragment[:m.start()], year
    return fragment, year


def _match_se(stem):
    """Find a season/episode tag in either supported form.

    Returns ``(start, season, episodes)`` or ``None``.  'SxxExx' is tried first
    so a file carrying both styles favours the explicit one.
    """
    for pattern in (_SXXEXX, _NXEE):
        m = pattern.search(stem)
        if not m:
            continue
        season = int(m.group("season"))
        episodes = [int(m.group("ep"))]
        for extra_ep in re.findall(r"\d{1,3}", m.group("extra") or ""):
            n = int(extra_ep)
            # A genuine multi-episode tag ascends (E21E22).  Anything not
            # greater than the previous episode is a title fragment that merely
            # looks like an episode - e.g. a title of "E2" after S03E21 - so
            # stop here rather than inventing a nonsensical two-parter.
            if n <= episodes[-1]:
                break
            episodes.append(n)
        return m.start(), season, episodes
    return None


def parse_filename(filename):
    """Parse one filename (full path or bare name) into a ``ParsedName``."""
    base = os.path.basename(filename)

    stem, dot, ext = base.rpartition(".")
    if not dot:                       # no extension at all
        stem, ext = base, ""
    ext = ext.lower()

    found = _match_se(stem)
    if found is None:
        # No season/episode tag - hand back a best-effort show name only.
        name, year = _split_year(stem)
        cleaned = _clean(name) or _clean(stem)
        return ParsedName(cleaned, None, [], year, ext, False)

    start, season, episodes = found
    before = stem[:start]
    name, year = _split_year(before)

    return ParsedName(_clean(name), season, episodes, year, ext, True)
