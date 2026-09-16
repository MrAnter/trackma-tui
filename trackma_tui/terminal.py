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

"""Asking the terminal for its real background colour, via OSC 11.

Textual's ``ansi_default`` resolves to near-black, leaving the app in a visibly
darker rectangle on a themed terminal. Works only before Textual starts, which
is when its input thread would swallow the reply.
"""

from __future__ import annotations

import re
import sys

QUERY = "\033]11;?\033\\"
START = "\033]11;"

# rgb:RRRR/GGGG/BBBB, though some terminals answer with 8- or 4-bit components.
_RESPONSE = re.compile(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")


def _component(raw: str) -> int:
    """Scale one hex component of any width down to 8 bits."""
    value = int(raw, 16)
    bits = len(raw) * 4
    if bits <= 8:
        return value << (8 - bits)
    return value >> (bits - 8)


def background_color(timeout: float = 0.2, attempts: int = 2) -> str | None:
    """The terminal background as ``#rrggbb``, or None. Never raises.

    Retried once: the read is known to be flaky if anything else touches stdin
    at the same moment.
    """
    if not sys.__stdin__ or not sys.__stdin__.isatty():
        return None

    try:
        from textual_image._terminal import capture_terminal_response, prepare_terminal_sequence
    except ImportError:
        return None

    for _ in range(max(1, attempts)):
        try:
            with capture_terminal_response(START, "\033\\", timeout) as response:
                sys.__stdout__.write(prepare_terminal_sequence(QUERY))
                sys.__stdout__.flush()
        except Exception:
            # Timeout, unexpected reply, or the BEL-terminated variant.
            continue

        match = _RESPONSE.search(response.sequence)
        if match:
            return "#%02x%02x%02x" % tuple(_component(part) for part in match.groups())
    return None
