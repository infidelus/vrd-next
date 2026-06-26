#!/usr/bin/env python3
"""Quick command-line check that your TMDB API key and the metadata path work.

Run it from the application's ``src`` directory:

    python3 addons/tmdb_check.py YOUR_API_KEY "Castle"
    python3 addons/tmdb_check.py YOUR_API_KEY "Castle" 5 11

The first form validates the key and lists the top TV matches.  The second also
looks up the given season/episode (here S05E11) on the best match and prints its
title - so you can confirm the whole chain end-to-end before the renamer UI is
built on top of it.

Get a free key at https://www.themoviedb.org/settings/api (v3 'API Key').
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tmdb_client import TmdbClient, TmdbError, year_of


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1

    api_key, query = argv[0], argv[1]
    client = TmdbClient(api_key)

    try:
        client.validate_key()
        print("API key looks good.\n")

        results = client.search_tv(query)
        if not results:
            print("No TV matches for %r." % query)
            return 0

        print("Top TV matches for %r:" % query)
        for r in results[:5]:
            yr = year_of(r.get("first_air_date")) or "----"
            print("  [%s] %s (%s)" % (r["id"], r.get("name", "?"), yr))

        if len(argv) >= 4:
            best = results[0]
            season, episode = int(argv[2]), int(argv[3])
            ep = client.tv_episode(best["id"], season, episode)
            print(
                "\nS%02dE%02d of %r -> %s"
                % (season, episode, best.get("name"), ep.get("name", "?"))
            )
    except TmdbError as e:
        print("Error: %s" % e)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
