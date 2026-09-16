# This file is part of trackma-tui.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Cover art fetching, sharing its on-disk cache with the GTK and Qt interfaces.

The engine only hands out a URL; every interface downloads its own. This one
reuses the GUIs' file naming so the cache is shared both ways.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from trackma import utils

USER_AGENT = "Trackma/{}".format(utils.VERSION)
TIMEOUT = 15

# Long enough not to hammer a dead URL on every scroll, short enough that a
# transient failure heals without a restart.
RETRY_AFTER = 120.0


def cover_path(api_info: dict, show: dict) -> str:
    """Cache path for *show*, using the GUIs' full-size ``_f_`` naming.

    The plain name is the list thumbnail, which the GUIs save at about 100x140 --
    too coarse for a terminal drawing real pixels.
    """
    return utils.to_cache_path(
        "%s_%s_f_%s.jpg" % (api_info["shortname"], api_info["mediatype"], show["id"])
    )


def cover_url(show: dict) -> str | None:
    """The largest cover URL the engine offers.

    ``image`` is the big one (460x642 on AniList), ``image_thumb`` the small.
    """
    return show.get("image") or show.get("image_thumb") or None


def _download(url: str, dest: str) -> None:
    """Fetch *url* into *dest* at full resolution, via a temporary file.

    The cache is shared, so a half-written JPEG would be read by the GUIs.
    """
    utils.make_dir(utils.to_cache_path())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = response.read()

    tmp = "%s.%d.part" % (dest, os.getpid())
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, dest)


class CoverFetcher:
    """Downloads covers off the UI thread.

    A cache hit returns a path at once; a miss goes to a thread pool and comes
    back through *on_ready*.
    """

    def __init__(self, api_info: dict, on_ready: Callable[[str, str], None], workers: int = 2):
        self._api_info = api_info
        self._on_ready = on_ready
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cover")
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._failed: dict[str, float] = {}

    def get(self, show: dict) -> str | None:
        """Return the cover path for *show* if it is already cached.

        Otherwise start a download and return ``None``; *on_ready* is called
        with ``(showid, path)`` from a worker thread once the file is there.
        """
        path = cover_path(self._api_info, show)
        if os.path.isfile(path):
            return path

        url = cover_url(show)
        if not url:
            return None

        showid = str(show["id"])
        with self._lock:
            if showid in self._pending:
                return None
            failed_at = self._failed.get(showid)
            if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER:
                return None
            self._failed.pop(showid, None)
            self._pending.add(showid)

        self._pool.submit(self._work, showid, url, path)
        return None

    def _work(self, showid: str, url: str, path: str) -> None:
        try:
            _download(url, path)
        except (urllib.error.URLError, OSError, TimeoutError):
            # Note the failure so scrolling won't retry it constantly.
            with self._lock:
                self._pending.discard(showid)
                self._failed[showid] = time.monotonic()
            return

        with self._lock:
            self._pending.discard(showid)
        self._on_ready(showid, path)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
