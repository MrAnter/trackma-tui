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

"""Parsing for the ``:`` command line.

Vim-shaped on purpose: ``:q`` refuses to quit with unsent changes, ``:q!`` goes
anyway, ``:wq`` sends them first. That carries the warning about Trackma's
local queue without a dialog box to explain it.
"""

from __future__ import annotations

from typing import NamedTuple

# name -> (canonical, takes an argument, one-line help)
COMMANDS: dict[str, tuple[str, bool, str]] = {
    "q": ("quit", False, "quit; refuses while changes are queued"),
    "quit": ("quit", False, ""),
    "q!": ("quit!", False, "quit anyway, discarding the queue"),
    "quit!": ("quit!", False, ""),
    "wq": ("sync-quit", False, "send the queue, then quit"),
    "x": ("sync-quit", False, ""),
    "w": ("sync", False, "send queued changes"),
    "sync": ("sync", False, ""),
    "fetch": ("fetch", False, "re-download the list from the site"),
    "ep": ("episode", True, "set the episode: :ep 13"),
    "score": ("score", True, "set the score: :score 8.5"),
    "status": ("status", True, "change status: :status watching"),
    "filter": ("filter", True, "filter by status: :filter all"),
    "f": ("filter", True, ""),
    "sort": ("sort", True, "sort: title, watched, available, next, score"),
    "play": ("play", True, "play the next episode, or :play 5"),
    "info": ("info", False, "open the show's details page"),
    "open": ("open", False, "open the show's web page"),
    "folder": ("folder", False, "open the folder the show lives in"),
    "help": ("help", False, "list keys and commands"),
}


class Command(NamedTuple):
    name: str
    arg: str


class CommandError(Exception):
    """The line typed isn't a command we know."""


def parse(line: str) -> Command:
    """Turn a typed command line into a :class:`Command`.

    ``+1`` and ``-2`` are shorthands for a relative episode change.
    """
    line = line.strip()
    if not line:
        raise CommandError("")

    if line[0] in "+-":
        delta = line[1:].strip() or "1"
        if not delta.isdigit():
            raise CommandError("%s needs a number" % line[0])
        return Command("delta", line[0] + delta)

    word, _, arg = line.partition(" ")
    entry = COMMANDS.get(word.lower())
    if entry is None:
        raise CommandError("unknown command: %s   (:help for the list)" % word)

    canonical, takes_arg, _help = entry
    arg = arg.strip()
    if arg and not takes_arg:
        raise CommandError(":%s takes no argument" % word)
    return Command(canonical, arg)


def help_lines() -> list[tuple[str, str]]:
    """Return ``(usage, description)`` for every documented command."""
    out = [(":+1  :-1  :+3", "add or remove episodes")]
    for name, (_canonical, takes_arg, text) in COMMANDS.items():
        if text:
            out.append((":%s%s" % (name, " …" if takes_arg else ""), text))
    return out
