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

"""Turning the engine's raw ``extra`` fields into something readable.

What comes back is untouched API output: Python lists, enums, and a synopsis
full of ``<br>`` tags and HTML entities.
"""

from __future__ import annotations

import html
import re

# Fields that repeat what the list already shows, or that read as noise.
SKIP = {"Type", "Status"}

# Order matters: these come first, in this order, and anything else follows.
PREFERRED = ["English", "Romaji", "Japanese", "Synonyms", "Season", "Genres", "Studios"]

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_BLANKS = re.compile(r"\n{3,}")


def clean_text(value: str) -> str:
    """Strip the HTML an API synopsis arrives wrapped in."""
    text = _BREAK.sub("\n", value)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return _BLANKS.sub("\n\n", text).strip()


def format_value(value: object) -> str:
    """Render one ``extra`` value as plain text."""
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(item) for item in value)
    return clean_text(str(value))


def fields(details: dict | None) -> list[tuple[str, str]]:
    """The ``extra`` fields as ``(label, text)``, ordered and cleaned.

    The synopsis is excluded; :func:`synopsis` returns it separately.
    """
    if not details:
        return []

    extras = {key: value for key, value in details.get("extra", []) if value}
    out: list[tuple[str, str]] = []

    for key in PREFERRED:
        if key in extras:
            out.append((key, format_value(extras.pop(key))))

    for key, value in extras.items():
        if key in SKIP or key.lower() == "synopsis":
            continue
        out.append((key, format_value(value)))

    return [(key, text) for key, text in out if text]


def synopsis(details: dict | None) -> str:
    """Return the cleaned synopsis, or an empty string."""
    if not details:
        return ""
    for key, value in details.get("extra", []):
        if key.lower() == "synopsis" and value:
            return format_value(value)
    return ""
