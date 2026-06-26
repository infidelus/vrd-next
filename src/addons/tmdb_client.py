"""A minimal TMDB (v3) API client using only the Python standard library.

The user supplies their own free TMDB API key (from their themoviedb.org
account); nothing is bundled.  Requests go out via urllib, so there are no
third-party dependencies to install.

Every failure surfaces as a ``TmdbError`` with a message fit to show the user.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.themoviedb.org/3"
_TIMEOUT = 15


class TmdbError(Exception):
    """Anything that stops a TMDB request returning usable data."""


class TmdbClient:
    def __init__(self, api_key, timeout=_TIMEOUT):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    # -- low level ---------------------------------------------------------
    def _build_url(self, path, params):
        query = dict(params or {})
        query["api_key"] = self.api_key
        return "%s%s?%s" % (API_ROOT, path, urllib.parse.urlencode(query))

    def _get(self, path, **params):
        if not self.api_key:
            raise TmdbError("No TMDB API key set - add one in Settings.")

        url = self._build_url(path, params)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TmdbError(
                    "TMDB rejected the API key (401) - check it in Settings."
                )
            if e.code == 404:
                raise TmdbError("TMDB has no entry for that request (404).")
            raise TmdbError("TMDB request failed (HTTP %s)." % e.code)
        except urllib.error.URLError as e:
            raise TmdbError("Couldn't reach TMDB: %s" % e.reason)
        except (ValueError, json.JSONDecodeError):
            raise TmdbError("TMDB returned a response that couldn't be read.")

    # -- validation --------------------------------------------------------
    def validate_key(self):
        """Return True if the key works, else raise ``TmdbError``."""
        self._get("/configuration")
        return True

    # -- TV ----------------------------------------------------------------
    def search_tv(self, query, year=None):
        params = {"query": query}
        if year:
            params["first_air_date_year"] = year
        return self._get("/search/tv", **params).get("results", [])

    def tv_details(self, tv_id):
        return self._get("/tv/%d" % int(tv_id))

    def tv_episode(self, tv_id, season, episode):
        return self._get(
            "/tv/%d/season/%d/episode/%d"
            % (int(tv_id), int(season), int(episode))
        )

    def tv_season(self, tv_id, season):
        """Every episode of one season in a single request.

        Returns the season payload; ``["episodes"]`` is a list of dicts each
        carrying ``episode_number``, ``name`` and ``air_date``.
        """
        return self._get("/tv/%d/season/%d" % (int(tv_id), int(season)))

    # -- Movies ------------------------------------------------------------
    def search_movie(self, query, year=None):
        params = {"query": query}
        if year:
            params["year"] = year
        return self._get("/search/movie", **params).get("results", [])

    def movie_details(self, movie_id):
        return self._get("/movie/%d" % int(movie_id))


def year_of(date_str):
    """'2009-03-09' -> 2009 (int), or None for empty/odd values."""
    if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None
