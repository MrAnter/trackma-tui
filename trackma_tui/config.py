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

"""User configuration, stored like the other Trackma interfaces'.

``~/.config/trackma/ui-textual.json``, read with
:func:`trackma.utils.parse_config`, which writes the defaults out if missing.

The nested dict is named ``colors`` on purpose: that is the one key parse_config
merges rather than replaces.
"""

from __future__ import annotations

from trackma import utils

CONFIG_FILE = "ui-textual.json"

# 65-75% lightness throughout, so it works on dark and light terminals alike.
# The stock curses palette fails this: "dark blue" on near-black vanishes.
DEFAULT_COLORS = {
    "airing": "#6cb6ff",       # still running
    "notaired": "#e3b341",     # not yet started
    "neweps": "#7ee787",       # unwatched episode sitting in the library
    "queued": "#d2a8ff",       # changed locally, not synced yet
    "playing": "#ff9e64",      # currently open in the player
    "normal": "#c9d1d9",       # everything else
    "dim": "#8b949e",          # secondary text
    "cursor": "#1f2937",       # the bar under the selected row
    "background": "#0d1117",   # only used when transparent_background is false
    "accent": "#6cb6ff",
    "warning": "#e3b341",
}

DEFAULT_CONFIG = {
    "colors": DEFAULT_COLORS,
    # Left to right. Available: title, progress, bar, aired, library, next,
    # score, status, type. "title" is always shown and takes the leftover width.
    "columns": ["title", "progress", "bar", "next", "score"],
    # Cover width in cells; height follows the aspect ratio.
    "cover_width": 30,
    # Match the terminal's own background; false paints "background" instead.
    "transparent_background": True,
    # auto | tgp | sixel | halfcell | unicode
    "image_protocol": "auto",
    # As named by the API (e.g. CURRENT) or by its label (watching).
    "default_filter": "CURRENT",
    # title | watched | available | aired | local | next | score | updated
    "default_sort": "title",
}


def load() -> dict:
    """Read the config file, creating it with the defaults if it's missing."""
    return utils.parse_config(utils.to_config_path(CONFIG_FILE), DEFAULT_CONFIG)
