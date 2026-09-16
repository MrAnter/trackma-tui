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

"""A Textual interface for Trackma, on top of ``trackma.engine``.

A fifth interface alongside ``cli``, ``curses``, ``gtk`` and ``qt``, not a fork:
the engine is shared and untouched.
"""

from __future__ import annotations

import datetime
import os
import threading
from typing import Any, Callable

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Input, Static
from textual_image.widget import HalfcellImage, SixelImage, TGPImage, UnicodeImage
from textual_image.widget import Image as AutoImage

from trackma import messenger, utils
from trackma.accounts import AccountManager
from trackma.engine import Engine

from trackma_tui import commands as cmd
from trackma_tui import config as tui_config
from trackma_tui import details as det
from trackma_tui import terminal
from trackma_tui.covers import CoverFetcher

IMAGE_WIDGETS = {
    "auto": AutoImage,
    "tgp": TGPImage,
    "sixel": SixelImage,
    "halfcell": HalfcellImage,
    "unicode": UnicodeImage,
}

# Hardcoded rather than locale-derived: strftime ignores the locale here.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _library_count(app: "TrackmaTui", show: dict) -> int:
    """How many episodes of *show* are actually on disk."""
    episodes = app._library.get(show["id"]) or app._library.get(str(show["id"]))
    return len(episodes) if episodes else 0


def _aired_count(show: dict) -> int:
    """How many episodes have been released, estimated by the engine."""
    try:
        return int(utils.estimate_aired_episodes(show) or 0)
    except (TypeError, ValueError):
        return 0


def _next_episode(show: dict) -> str:
    """When the next episode airs, as few characters as will still say it."""
    when = show.get("next_ep_time")
    if not when:
        return "–"
    try:
        days = (when.date() - datetime.date.today()).days
    except AttributeError:
        return "–"
    if days < 0:
        return "–"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return "%s %d/%d" % (WEEKDAYS[when.weekday()], when.day, when.month)
    return "%d/%d" % (when.day, when.month)


def _available_episode(app: "TrackmaTui", show: dict) -> int:
    """The highest episode number sitting in the library, not how many files."""
    episodes = app._library.get(show["id"]) or app._library.get(str(show["id"]))
    if not episodes:
        return 0
    try:
        return max(int(e) for e in episodes)
    except (TypeError, ValueError):
        return 0


def _progress_bar(app: "TrackmaTui", show: dict, width: int) -> Text:
    """Watched / downloaded / aired as three colours on one run of blocks.

    accent = watched, neweps = downloaded unwatched, warning = aired but not
    downloaded, dim dots = not aired. Shows with no episode total are scaled
    against the furthest known episode.
    """
    watched = show.get("my_progress") or 0
    available = max(_available_episode(app, show), watched)
    aired = max(_aired_count(show), available)
    total = show.get("total") or 0
    scale = total or aired or 1

    def cut(value: int) -> int:
        return max(0, min(width, round(value * width / scale)))

    ends = (cut(watched), cut(available), cut(aired))
    colors = (app.tk_colors["accent"], app.tk_colors["neweps"], app.tk_colors["warning"])

    bar = Text(no_wrap=True)
    start = 0
    for end, color in zip(ends, colors):
        if end > start:
            bar.append("█" * (end - start), style=color)
            start = end
    if start < width:
        bar.append("·" * (width - start), style=app.tk_colors["dim"])
    return bar


def _next_sort_key(show: dict) -> tuple[int, float]:
    """Sort by air date, with everything that has no date last."""
    when = show.get("next_ep_time")
    if not when:
        return (1, 0.0)
    try:
        return (0, when.timestamp())
    except (AttributeError, OSError, ValueError):
        return (1, 0.0)


# key -> (header, width or None for the flexible one, align, render, sort key)
COLUMNS: dict[str, tuple[str, int | None, str, Callable[[Any, dict], str], Callable[[Any, dict], Any]]] = {
    "title": ("title", None, "left", lambda a, s: s["title"], lambda a, s: s["title"].lower()),
    "progress": (
        "watched",
        9,
        "right",
        lambda a, s: "%s/%s" % (s.get("my_progress", 0), s.get("total") or "?"),
        lambda a, s: -(s.get("my_progress") or 0),
    ),
    "aired": (
        "aired",
        6,
        "right",
        lambda a, s: str(_aired_count(s) or "–"),
        lambda a, s: -_aired_count(s),
    ),
    "library": (
        "local",
        6,
        "right",
        lambda a, s: str(_library_count(a, s) or "–"),
        lambda a, s: -_library_count(a, s),
    ),
    "bar": (
        "available",
        14,
        "left",
        lambda a, s: _progress_bar(a, s, 13),
        lambda a, s: -_available_episode(a, s),
    ),
    "next": ("next", 9, "left", lambda a, s: _next_episode(s), lambda a, s: _next_sort_key(s)),
    "score": (
        "score",
        5,
        "right",
        lambda a, s: ("%g" % s["my_score"]) if s.get("my_score") else "–",
        lambda a, s: -(float(s.get("my_score") or 0)),
    ),
    "status": ("status", 15, "left", lambda a, s: str(s["status"]), lambda a, s: str(s["status"])),
    "type": ("type", 8, "left", lambda a, s: str(s.get("type") or "?"), lambda a, s: str(s.get("type") or "")),
}

# Sorting reuses the columns' key functions, on screen or not.
SORT_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "progress": ("watched", "progress"),
    "aired": ("aired",),
    "library": ("local", "library"),
    "bar": ("available", "bar"),
    "next": ("next", "airing"),
    "score": ("score",),
    "updated": ("updated", "modified"),
}


def sort_key(name: str) -> str | None:
    """Resolve a user-typed sort name to a column key."""
    name = name.lower()
    for key, aliases in SORT_ALIASES.items():
        if name == key or name in aliases:
            return key
    return None


ALL_FILTER = "__all__"
STATUS_TIMEOUT = 5.0


class SegmentBar(Static):
    """A line of clickable words: the filter row and the key legend."""

    class Selected(Message):
        def __init__(self, bar: "SegmentBar", payload: Any) -> None:
            self.bar = bar
            self.payload = payload
            super().__init__()

        @property
        def control(self) -> "SegmentBar":
            """The bar clicked, so @on(...) can select between the two."""
            return self.bar

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._ranges: list[tuple[int, int, Any]] = []

    def set_segments(self, segments: list[tuple[str, Any, str]], gap: str = "   ") -> None:
        """Draw ``(label, payload, style)`` and record where each landed."""
        text = Text(" ")
        self._ranges = []
        for index, (label, payload, style) in enumerate(segments):
            if index:
                text.append(gap)
            start = len(text.plain)
            text.append(label, style=style)
            self._ranges.append((start, len(text.plain), payload))
        self.update(text)

    def on_click(self, event) -> None:
        for start, end, payload in self._ranges:
            if start <= event.x < end:
                self.post_message(self.Selected(self, payload))
                return


class PagerScreen(Screen):
    """A full-screen reader: details and help both use it."""

    BINDINGS = [
        Binding("escape,q", "close", "close"),
        Binding("j,down", "scroll_down", "down", show=False),
        Binding("k,up", "scroll_up", "up", show=False),
    ]

    def __init__(self, title: str, cover: str | None = None):
        super().__init__()
        self._title = title
        self._cover = cover
        # push_screen() returns before the screen mounts; hold text till then.
        self._summary: Text | str = ""
        self._content: Text | str = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="pager-body"):
            with Horizontal(id="pager-head"):
                if self._cover:
                    image_widget = IMAGE_WIDGETS.get(self.app.config["image_protocol"], AutoImage)
                    yield image_widget(self._cover, id="pager-cover")
                with Vertical(id="pager-headtext"):
                    yield Static(self._title, id="pager-title")
                    yield Static("", id="pager-summary")
            yield Static("", id="pager-content")
        yield Static("  q / Esc to go back", id="pager-status")

    def on_mount(self) -> None:
        self._apply()

    def set_summary(self, text: Text | str) -> None:
        self._summary = text
        self._apply()

    def set_content(self, text: Text | str) -> None:
        self._content = text
        self._apply()

    def _apply(self) -> None:
        """Push held text into the widgets once they exist.

        is_mounted is not a reliable gate here, so try the query instead and
        let on_mount retry.
        """
        try:
            summary = self.query_one("#pager-summary", Static)
            content = self.query_one("#pager-content", Static)
        except NoMatches:
            return  # not composed yet; on_mount will apply what we held
        summary.update(self._summary)
        content.update(self._content)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one("#pager-body", VerticalScroll).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#pager-body", VerticalScroll).scroll_up()


class TrackmaTui(App):
    """The application."""

    CSS_PATH = "app.tcss"
    TITLE = "Trackma"

    BINDINGS = [
        Binding("colon", "command", "command", key_display=":"),
        Binding("slash", "search", "search"),
        Binding("q", "quit_asked", "quit"),
        Binding("question_mark", "help", "help"),
        Binding("plus,equals_sign", "episode_delta(1)", "+1 ep"),
        Binding("minus", "episode_delta(-1)", "-1 ep"),
        Binding("u", "prefill('ep ')", "episode", show=False),
        Binding("z", "prefill('score ')", "score", show=False),
        Binding("t", "prefill('status ')", "status", show=False),
        Binding("p", "play", "play", show=False),
        Binding("s", "sync", "send", show=False),
        Binding("i,enter", "details", "details", show=False),
        Binding("O", "open_web", "website", show=False),
        Binding("o", "open_folder", "folder", show=False),
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("g", "cursor_top", "top", show=False),
        Binding("G", "cursor_bottom", "bottom", show=False),
        Binding("shift+left", "prev_filter", "prev filter", show=False),
        Binding("shift+right", "next_filter", "next filter", show=False),
    ]

    def __init__(self, account: dict, terminal_background: str | None = None):
        # Needed before App.__init__: it builds the stylesheet immediately.
        self.account = account
        self.terminal_background = terminal_background
        self.config = tui_config.load()
        # Not `self.colors`: Textual's DOMNode already owns that name.
        self.tk_colors = self.config["colors"]
        super().__init__()

        self.engine: Engine | None = None
        self._engine_lock = threading.Lock()
        self._main_thread = threading.get_ident()

        self._shows: dict[str, dict] = {}
        self._library: dict = {}
        self._status_filters: list[tuple[str, Any]] = []
        self._filter: Any = ALL_FILTER
        self._filter_label = ""
        self._sort = "title"
        self._sort_reverse = False
        self._search = ""
        self._playing: set[str] = set()
        self._tracker: dict | None = None
        self._selected: str | None = None
        self._covers: CoverFetcher | None = None
        self._title_width = 40
        self._columns: dict[str, Any] = {}
        self._column_keys: list[str] = []
        self._mode = ":"
        self._status_timer = None

    def get_css_variables(self) -> dict[str, str]:
        """Expose the user's palette to the stylesheet as ``$tk-*``."""
        variables = super().get_css_variables()
        variables.update({"tk-%s" % name: value for name, value in self.tk_colors.items()})
        # ansi_default is not a substitute: it resolves to near-black.
        if self.config.get("transparent_background", True) and self.terminal_background:
            variables["tk-bg"] = self.terminal_background
        elif self.config.get("transparent_background", True):
            variables["tk-bg"] = "ansi_default"
        else:
            variables["tk-bg"] = self.tk_colors.get("background", "#0d1117")
        return variables

    def compose(self) -> ComposeResult:
        yield SegmentBar(id="filters")
        with Horizontal(id="body"):
            # Default "css" repaints the whole row, flattening the bar.
            yield DataTable(
                id="table",
                cursor_type="row",
                show_header=True,
                zebra_stripes=False,
                cursor_foreground_priority="renderable",
            )
            with Vertical(id="side"):
                image_widget = IMAGE_WIDGETS.get(self.config["image_protocol"], AutoImage)
                yield image_widget(id="cover")
                yield Static("", id="cover-missing")
                yield Static("", id="side-meta")
        yield Static("", id="status")
        with Horizontal(id="cmdline"):
            yield Static(":", id="cmdprompt")
            yield Input(id="cmd")
        yield SegmentBar(id="legend")

    LEGEND = [
        ("j k", "move", "cursor_down"),
        ("/", "search", "search"),
        (":", "command", "command"),
        ("+ -", "episodes", "episode_delta(1)"),
        ("p", "play", "play"),
        ("i", "details", "details"),
        ("s", "send", "sync"),
        ("?", "help", "help"),
        ("q", "quit", "quit_asked"),
    ]

    def _draw_legend(self) -> None:
        segments = []
        for key, label, action in self.LEGEND:
            segments.append(
                (
                    "%s %s" % (key, label),
                    action,
                    self.tk_colors["dim"],
                )
            )
        self.query_one("#legend", SegmentBar).set_segments(segments)

    def _draw_filters(self) -> None:
        segments = []
        for label, value in self._status_filters:
            active = value == self._filter
            segments.append(
                (
                    label.lower(),
                    value,
                    "bold %s" % self.tk_colors["accent"] if active else self.tk_colors["dim"],
                )
            )
        self.query_one("#filters", SegmentBar).set_segments(segments, gap="  ")

    @on(SegmentBar.Selected, "#filters")
    def _filter_clicked(self, event: SegmentBar.Selected) -> None:
        for label, value in self._status_filters:
            if value == event.payload:
                self._filter, self._filter_label = value, label
                self._rebuild_table()
                return

    @on(SegmentBar.Selected, "#legend")
    async def _legend_clicked(self, event: SegmentBar.Selected) -> None:
        # run_action is a coroutine: calling it without awaiting silently does
        # nothing but emit a "never awaited" warning.
        await self.run_action(event.payload)

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        wanted = [c for c in self.config.get("columns", []) if c in COLUMNS]
        if "title" not in wanted:
            wanted.insert(0, "title")
        self._column_keys = wanted
        for key in wanted:
            header, width, _align, _render, _sort = COLUMNS[key]
            if width is not None:
                # Room for the sort marker, or it falls off the last column.
                width = max(width, len(header) + 2)
            self._columns[key] = table.add_column(
                header, width=self._title_width if width is None else width
            )
        self._sort = sort_key(str(self.config["default_sort"])) or "title"
        self.query_one("#cover").display = False
        self._draw_legend()
        self.set_status("starting…")
        self._boot()

    # ------------------------------------------------- thread marshalling

    def _ui(self, callback: Callable, *args: Any) -> None:
        """Run *callback* on the UI thread, wherever we're called from."""
        if threading.get_ident() == self._main_thread:
            callback(*args)
        else:
            try:
                self.call_from_thread(callback, *args)
            except RuntimeError:
                # The app is already shutting down; nothing left to update.
                pass

    def _message_handler(self, classname: str, msgtype: int, msg: str) -> None:
        if msgtype == messenger.TYPE_DEBUG:
            return
        self._ui(self.set_status, msg, msgtype == messenger.TYPE_WARN)

    @work(thread=True, exclusive=True, group="engine")
    def _boot(self) -> None:
        engine = Engine(self.account, self._message_handler)
        for signal in ("episode_changed", "score_changed", "status_changed", "show_synced"):
            engine.connect_signal(signal, self._on_show_changed)
        engine.connect_signal("show_added", self._on_list_changed)
        engine.connect_signal("show_deleted", self._on_list_changed)
        engine.connect_signal("queue_changed", self._on_queue_changed)
        engine.connect_signal("playing", self._on_playing)
        engine.connect_signal("tracker_state", self._on_tracker_state)

        with self._engine_lock:
            engine.start()

        self.engine = engine
        self._ui(self._engine_ready)

    def _engine_ready(self) -> None:
        engine = self.engine
        assert engine is not None

        self._covers = CoverFetcher(engine.api_info, self._on_cover_ready)

        labels = engine.mediainfo["statuses_dict"]
        self._status_filters = [(labels[s], s) for s in engine.mediainfo["statuses"]]
        self._status_filters.append(("all", ALL_FILTER))

        wanted = str(self.config["default_filter"]).lower()
        index = next(
            (
                i
                for i, (label, value) in enumerate(self._status_filters)
                if str(value).lower() == wanted or label.lower() == wanted
            ),
            0,
        )
        self._filter, self._filter_label = self._status_filters[index][1], self._status_filters[index][0]

        self._tracker = engine.tracker_status()

        # The first resize lands before the table exists.
        self._fit_columns()
        self._reload_shows()
        self.query_one("#table", DataTable).focus()

    # ----------------------------------------------------- engine callbacks

    def _on_show_changed(self, show: dict, *_extra) -> None:
        # status_changed and show_synced carry a second argument
        self._ui(self._refresh_row, str(show["id"]))

    def _on_list_changed(self, show: dict) -> None:
        self._ui(self._reload_shows)

    def _on_queue_changed(self, queue: list) -> None:
        self._ui(self._refresh_status)

    def _on_playing(self, show: dict, is_playing: bool, episode: int) -> None:
        showid = str(show["id"])
        if is_playing:
            self._playing.add(showid)
        else:
            self._playing.discard(showid)
        self._ui(self._refresh_row, showid)

    def _on_tracker_state(self, status: dict) -> None:
        self._tracker = status
        self._ui(self._refresh_status)

    def _on_cover_ready(self, showid: str, path: str) -> None:
        self._ui(self._show_cover, showid, path)

    # ------------------------------------------------------------ list model

    def _reload_shows(self) -> None:
        engine = self.engine
        if engine is None:
            return
        self._shows = {str(s["id"]): s for s in engine.get_list()}
        try:
            self._library = engine.library()
        except Exception:
            # Playing is optional; a library we can't read just means no
            # "new episode" highlighting.
            self._library = {}
        self._rebuild_table()

    def _visible_shows(self) -> list[dict]:
        shows = self._shows.values()
        if self._filter != ALL_FILTER:
            shows = [s for s in shows if s["my_status"] == self._filter]
        if self._search:
            needle = self._search.lower()
            shows = [s for s in shows if needle in s["title"].lower()]
        key_fn = COLUMNS[self._sort][4] if self._sort in COLUMNS else None
        if key_fn is None:  # "updated" isn't a column, only a sort
            key_fn = lambda a, s: str(s.get("my_last_update") or "")
        return sorted(shows, key=lambda s: key_fn(self, s), reverse=self._sort_reverse)

    def _new_episodes(self, show: dict) -> bool:
        """True if the library holds an episode past the recorded progress."""
        episodes = self._library.get(show["id"]) or self._library.get(str(show["id"]))
        if not episodes:
            return False
        try:
            return max(int(e) for e in episodes) > (show.get("my_progress") or 0)
        except (TypeError, ValueError):
            return False

    def _row_color(self, show: dict) -> str:
        showid = str(show["id"])
        if showid in self._playing:
            return self.tk_colors["playing"]
        if show.get("queued"):
            return self.tk_colors["queued"]
        if self._new_episodes(show):
            return self.tk_colors["neweps"]
        if show["status"] == utils.Status.ONGOING:
            return self.tk_colors["airing"]
        if show["status"] == utils.Status.NOTYET:
            return self.tk_colors["notaired"]
        return self.tk_colors["normal"]

    def _row_cells(self, show: dict) -> list[Text]:
        color = self._row_color(show)
        showid = str(show["id"])
        bold = bool(show.get("queued")) or showid in self._playing
        style = "bold %s" % color if bold else color

        cells = []
        for key in self._column_keys:
            _header, width, align, render, _sort = COLUMNS[key]
            text = render(self, show)
            if isinstance(text, Text):
                cells.append(text)
                continue
            if key == "title":
                marker = "> " if showid == self._selected else "  "
                room = self._title_width - len(marker)
                if len(text) > room:
                    text = text[: room - 1] + "…"
                cells.append(Text(marker + text, style=style, no_wrap=True))
                continue
            if width:
                text = text.rjust(width - 1) if align == "right" else text.ljust(width - 1)
            cells.append(Text(text, style=color, no_wrap=True))
        return cells

    def _rebuild_table(self) -> None:
        table = self.query_one("#table", DataTable)
        previous = self._selected
        rows = self._visible_shows()
        keep = previous if any(str(s["id"]) == previous for s in rows) else None
        self._selected = keep or (str(rows[0]["id"]) if rows else None)

        table.clear()
        for show in rows:
            table.add_row(*self._row_cells(show), key=str(show["id"]))

        if self._selected:
            table.move_cursor(row=table.get_row_index(self._selected))
        self._update_side(self._shows.get(self._selected) if self._selected else None)
        self._draw_filters()
        self._draw_headers()
        self._refresh_status()

    def _refresh_row(self, showid: str) -> None:
        show = self._shows.get(showid)
        if show is None:
            return
        table = self.query_one("#table", DataTable)
        try:
            for key, cell in zip(self._column_keys, self._row_cells(show)):
                table.update_cell(showid, self._columns[key], cell)
        except Exception:
            # The row isn't on screen under the current filter; the next
            # rebuild will pick the change up.
            return
        if showid == self._selected:
            self._update_side(show)

    def _fit_columns(self) -> None:
        """Give the title column whatever the others don't need.

        DataTable has no fractional widths, so measure the leftovers.
        """
        table = self.query_one("#table", DataTable)
        title_key = self._columns["title"]
        used = sum(
            column.get_render_width(table)
            for key, column in table.columns.items()
            if key != title_key
        )
        padding = table.cell_padding * 2
        width = max(20, table.size.width - used - padding - table.scrollbar_size_vertical)
        if width == self._title_width:
            return
        self._title_width = width
        column = table.columns.get(title_key)
        if column is not None:
            column.width = width
            column.auto_width = False
        self._rebuild_table()

    def on_resize(self) -> None:
        if self.engine is not None:
            self._fit_columns()

    # --------------------------------------------------------- side panel

    def _update_side(self, show: dict | None) -> None:
        meta = self.query_one("#side-meta", Static)
        if show is None:
            meta.update("")
            self._show_cover(None, None)
            return

        lines = [
            Text(show["title"], style="bold %s" % self._row_color(show)),
            Text(""),
            Text(
                "%s / %s episodes" % (show.get("my_progress", 0), show.get("total") or "?"),
                style=self.tk_colors["dim"],
            ),
            Text(
                "score %s" % (("%g" % show["my_score"]) if show.get("my_score") else "-"),
                style=self.tk_colors["dim"],
            ),
            Text(
                "%s · %s" % (show.get("type") or "?", show["status"]),
                style=self.tk_colors["dim"],
            ),
        ]
        aired = _aired_count(show)
        available = _available_episode(self, show)
        if aired or available:
            lines.append(
                Text(
                    "aired %s · local up to %s"
                    % (aired or "?", available or "–"),
                    style=self.tk_colors["dim"],
                )
            )
        if show.get("next_ep_time"):
            lines.append(
                Text(
                    "ep %s: %s" % (show.get("next_ep_number") or "?", _next_episode(show)),
                    style=self.tk_colors["dim"],
                )
            )
        if show.get("queued"):
            lines.append(Text("change not sent", style=self.tk_colors["queued"]))

        meta.update(Text("\n").join(lines))
        self._update_cover(show)

    def _update_cover(self, show: dict) -> None:
        if self._covers is None:
            return
        self._show_cover(str(show["id"]), self._covers.get(show))

    def _show_cover(self, showid: str | None, path: str | None) -> None:
        if showid is not None and showid != self._selected:
            return  # the user moved on while this was downloading
        cover = self.query_one("#cover")
        missing = self.query_one("#cover-missing", Static)
        if path and os.path.isfile(path):
            try:
                cover.image = path
            except Exception:
                path = None
        if path and os.path.isfile(path):
            cover.display = True
            missing.update("")
        else:
            cover.display = False
            missing.update(Text("(no cover)", style=self.tk_colors["dim"]))

    # ------------------------------------------------------------ status bar

    def _queue_size(self) -> int:
        return len(self.engine.get_queue()) if self.engine else 0

    def _refresh_status(self) -> None:
        """What's listed, how it's sorted, and what's still queued."""
        if self.engine is None:
            return
        table = self.query_one("#table", DataTable)
        parts = [
            Text("%d %s" % (table.row_count, self._filter_label.lower()), style=self.tk_colors["dim"]),
            Text(
                "sort: %s %s" % (SORT_ALIASES[self._sort][0], self._sort_marker()),
                style=self.tk_colors["dim"],
            ),
        ]
        if self._search:
            parts.append(Text("search: %s" % self._search, style=self.tk_colors["accent"]))
        queued = self._queue_size()
        if queued:
            parts.append(
                Text(
                    "%d queued (:w to send)" % queued,
                    style="bold %s" % self.tk_colors["warning"],
                )
            )
        tracker = self._tracker_text()
        if tracker is not None:
            parts.append(tracker)
        self.query_one("#status", Static).update(
            Text(" · ", style=self.tk_colors["dim"]).join(parts)
        )

    def _tracker_text(self) -> Text | None:
        """What the tracker is doing, the way the Qt status bar spells it."""
        status = self._tracker
        if not status:
            return None
        state = status["state"]
        if state == utils.Tracker.NOVIDEO:
            return Text("tracker: listening", style=self.tk_colors["dim"])
        if state == utils.Tracker.UNRECOGNIZED:
            return Text("tracker: file name not recognized", style=self.tk_colors["warning"])
        if state == utils.Tracker.IGNORED:
            return Text("tracker: ignored", style=self.tk_colors["dim"])

        show, episode = status["show"] or (None, None)
        timer = status["timer"]
        if timer is None:
            when = ""
        elif timer > 0:
            when = "in %d:%02d" % divmod(timer, 60)
            if status["paused"]:
                when += " [paused]"
        elif self.engine and self.engine.config["tracker_update_close"]:
            when = "when the player closes"
        else:
            when = "sent"
        if state == utils.Tracker.NOT_FOUND:
            return Text(
                "tracker: %s not in list" % (show["title"] if show else "show"),
                style=self.tk_colors["warning"],
            )
        title = show["title"] if show else "?"
        return Text("▶ %s ep %s · update %s" % (title, episode, when), style=self.tk_colors["accent"])

    def _sort_marker(self) -> str:
        return "▴" if self._sort_reverse else "▾"

    def _draw_headers(self) -> None:
        """Mark the column currently sorted on."""
        table = self.query_one("#table", DataTable)
        for key in self._column_keys:
            column = table.columns.get(self._columns[key])
            if column is None:
                continue
            label = COLUMNS[key][0]
            if key == self._sort:
                label = "%s %s" % (label, self._sort_marker())
            column.label = Text(label, style=self.tk_colors["accent"] if key == self._sort else "")
        table.refresh()

    def set_status(self, message: str, warning: bool = False) -> None:
        """Show a transient message, then fall back to the permanent line."""
        self.query_one("#status", Static).update(
            Text(message, style=self.tk_colors["warning"] if warning else self.tk_colors["dim"])
        )
        if self._status_timer is not None:
            self._status_timer.stop()
        self._status_timer = self.set_timer(STATUS_TIMEOUT, self._refresh_status)

    # --------------------------------------------------------- command line

    def _open_cmdline(self, mode: str, prefill: str = "") -> None:
        self._mode = mode
        self.query_one("#cmdprompt", Static).update(mode)
        line = self.query_one("#cmdline")
        line.add_class("visible")
        self.query_one("#status").display = False
        field = self.query_one("#cmd", Input)
        field.value = prefill
        field.focus()
        field.cursor_position = len(prefill)

    def _close_cmdline(self) -> None:
        self.query_one("#cmdline").remove_class("visible")
        self.query_one("#status").display = True
        self.query_one("#cmd", Input).value = ""
        self.query_one("#table", DataTable).focus()

    def action_command(self) -> None:
        self._open_cmdline(":")

    def action_search(self) -> None:
        self._open_cmdline("/", self._search)

    def action_prefill(self, text: str) -> None:
        self._open_cmdline(":", text)
        if text.startswith("status") and self.engine is not None:
            names = " ".join(label.lower() for label, _ in self._status_filters[:-1])
            self.set_status("statuses: %s" % names)

    @on(Input.Changed, "#cmd")
    def _cmd_changed(self, event: Input.Changed) -> None:
        if self._mode == "/":
            self._search = event.value.strip()
            self._rebuild_table()

    @on(Input.Submitted, "#cmd")
    def _cmd_submitted(self, event: Input.Submitted) -> None:
        line = event.value
        self._close_cmdline()
        if self._mode == "/":
            return
        self._run_command(line)

    def on_key(self, event) -> None:
        if event.key == "escape" and self.query_one("#cmdline").has_class("visible"):
            was_search = self._mode == "/"
            self._close_cmdline()
            if was_search and self._search:
                self._search = ""
                self._rebuild_table()
            event.stop()

    def _run_command(self, line: str) -> None:
        try:
            command = cmd.parse(line)
        except cmd.CommandError as error:
            if str(error):
                self.set_status(str(error), warning=True)
            return

        name, arg = command
        handlers: dict[str, Callable[[], None]] = {
            "quit": lambda: self.action_quit_asked(),
            "quit!": lambda: self.exit(),
            "sync": lambda: self._do_sync(),
            "sync-quit": lambda: self._do_sync(then_quit=True),
            "fetch": lambda: self._do_retrieve(),
            "delta": lambda: self.action_episode_delta(int(arg)),
            "episode": lambda: self._cmd_episode(arg),
            "score": lambda: self._cmd_score(arg),
            "status": lambda: self._cmd_status(arg),
            "filter": lambda: self._cmd_filter(arg),
            "sort": lambda: self._cmd_sort(arg),
            "play": lambda: self.action_play(arg),
            "info": lambda: self.action_details(),
            "open": lambda: self.action_open_web(),
            "folder": lambda: self.action_open_folder(),
            "help": lambda: self.action_help(),
        }
        handlers[name]()

    def _cmd_episode(self, arg: str) -> None:
        show = self._current()
        if show is None:
            return
        if not arg:
            self.set_status("usage: :ep 13", warning=True)
            return
        self._do_set_episode(show["id"], arg)

    def _cmd_score(self, arg: str) -> None:
        show = self._current()
        if show is None:
            return
        if not arg:
            self.set_status("usage: :score 8.5", warning=True)
            return
        self._do_set_score(show["id"], arg)

    def _cmd_status(self, arg: str) -> None:
        show = self._current()
        if show is None or self.engine is None:
            return
        labels = self.engine.mediainfo["statuses_dict"]
        wanted = arg.lower()
        for value, label in labels.items():
            if wanted in (label.lower(), str(value).lower()):
                self._do_set_status(show["id"], value)
                return
        self.set_status(
            "unknown status: %s   (%s)" % (arg, ", ".join(l.lower() for l in labels.values())),
            warning=True,
        )

    def _cmd_filter(self, arg: str) -> None:
        wanted = arg.lower()
        for label, value in self._status_filters:
            if wanted in (label.lower(), str(value).lower()):
                self._filter, self._filter_label = value, label
                self._rebuild_table()
                return
        self.set_status(
            "unknown filter: %s   (%s)"
            % (arg, ", ".join(label.lower() for label, _ in self._status_filters)),
            warning=True,
        )

    def _set_sort(self, key: str) -> None:
        """Sort by *key*, or flip the direction if it's already the one."""
        if key == self._sort:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort, self._sort_reverse = key, False
        self._rebuild_table()

    @on(DataTable.HeaderSelected, "#table")
    def _header_clicked(self, event: DataTable.HeaderSelected) -> None:
        key = self._column_keys[event.column_index]
        if key in SORT_ALIASES:
            self._set_sort(key)

    def _cmd_sort(self, arg: str) -> None:
        names = ", ".join(aliases[0] for aliases in SORT_ALIASES.values())
        if not arg:
            self.set_status("sorts: %s" % names, warning=True)
            return
        key = sort_key(arg)
        if key is None:
            self.set_status("unknown sort: %s   (%s)" % (arg, names), warning=True)
            return
        self._set_sort(key)

    # -------------------------------------------------------------- actions

    def _current(self) -> dict | None:
        show = self._shows.get(self._selected) if self._selected else None
        if show is None:
            self.set_status("no show selected", warning=True)
        return show

    @on(DataTable.RowHighlighted, "#table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        new = str(event.row_key.value) if event.row_key.value is not None else None
        if new == self._selected:
            return
        previous, self._selected = self._selected, new
        # Only the marker moved: repaint just those two rows.
        for showid in (previous, new):
            if showid and showid in self._shows:
                self._refresh_row(showid)
        self._update_side(self._shows.get(new) if new else None)

    def action_cursor_down(self) -> None:
        self.query_one("#table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#table", DataTable).action_cursor_up()

    def action_cursor_top(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count:
            table.move_cursor(row=0)

    def action_cursor_bottom(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count:
            table.move_cursor(row=table.row_count - 1)

    def _step_filter(self, step: int) -> None:
        if not self._status_filters:
            return
        current = next(
            (i for i, (_, v) in enumerate(self._status_filters) if v == self._filter), 0
        )
        index = (current + step) % len(self._status_filters)
        self._filter_label, self._filter = (
            self._status_filters[index][0],
            self._status_filters[index][1],
        )
        self._rebuild_table()

    def action_prev_filter(self) -> None:
        self._step_filter(-1)

    def action_next_filter(self) -> None:
        self._step_filter(1)

    def action_episode_delta(self, delta: int) -> None:
        show = self._current()
        if show is None:
            return
        target = (show.get("my_progress") or 0) + delta
        if target < 0:
            return
        self._do_set_episode(show["id"], target)

    def action_play(self, episode: str = "") -> None:
        show = self._current()
        if show is not None:
            self._do_play(show, episode)

    def action_sync(self) -> None:
        self._do_sync()

    def action_open_web(self) -> None:
        show = self._current()
        if show and show.get("url"):
            utils.spawn_process(["xdg-open", show["url"]])

    def action_open_folder(self) -> None:
        show = self._current()
        if show is not None and self.engine is not None:
            try:
                self.engine.open_show_folder(show["id"])
            except utils.TrackmaError as error:
                self.set_status(str(error), warning=True)

    def action_quit_asked(self) -> None:
        """Refuse to quit while changes are queued, the way ``:q`` does.

        Trackma never syncs on its own; quitting would drop the queue.
        """
        queued = self._queue_size()
        if queued:
            self.set_status(
                "%d queued changes — :w to send, :wq to send and quit, "
                ":q! to quit anyway" % queued,
                warning=True,
            )
            return
        self.exit()

    def action_help(self) -> None:
        screen = PagerScreen("Keys and commands")
        self.push_screen(screen)
        keys = [
            ("j k  ↑ ↓", "move through the list"),
            ("g G", "first / last show"),
            ("shift+← shift+→", "previous / next filter"),
            ("/", "search titles"),
            (":", "command line"),
            ("+ -", "one episode more / less"),
            ("u z t", "episode, score, status (opens the command line)"),
            ("p", "play the next episode"),
            ("i", "show details"),
            ("s", "send queued changes"),
            ("q", "quit"),
        ]
        block = [Text("keys", style="bold %s" % self.tk_colors["accent"]), Text("")]
        for key, text in keys:
            block.append(
                Text.assemble(
                    (key.ljust(18), self.tk_colors["normal"]), (text, self.tk_colors["dim"])
                )
            )
        block += [Text(""), Text("commands", style="bold %s" % self.tk_colors["accent"]), Text("")]
        for usage, text in cmd.help_lines():
            block.append(
                Text.assemble(
                    (usage.ljust(18), self.tk_colors["normal"]), (text, self.tk_colors["dim"])
                )
            )
        screen.set_content(Text("\n").join(block))

    def action_details(self) -> None:
        show = self._current()
        if show is None:
            return
        cover = self._covers.get(show) if self._covers else None
        screen = PagerScreen(show["title"], cover)
        self.push_screen(screen)
        summary = Text(
            "%s / %s episodi · voto %s · %s · %s"
            % (
                show.get("my_progress", 0),
                show.get("total") or "?",
                ("%g" % show["my_score"]) if show.get("my_score") else "-",
                show.get("type") or "?",
                show["status"],
            ),
            style=self.tk_colors["dim"],
        )
        screen.set_summary(summary)
        screen.set_content(Text("loading…", style=self.tk_colors["dim"]))
        self._do_details(show, screen, summary)

    def _render_details(
        self, screen: PagerScreen, summary: Text, data: dict | None
    ) -> None:
        """Fields beside the cover, synopsis full width below it."""
        if screen not in self.screen_stack:
            return  # the user already went back to the list

        block: list[Text] = [summary, Text("")]
        for label, text in det.fields(data):
            block.append(
                Text.assemble(
                    ("%s: " % label, "bold %s" % self.tk_colors["dim"]),
                    (text, self.tk_colors["normal"]),
                )
            )
        screen.set_summary(Text("\n").join(block))

        body = det.synopsis(data)
        screen.set_content(
            Text(body, style=self.tk_colors["normal"])
            if body
            else Text("no synopsis", style=self.tk_colors["dim"])
        )

    # ------------------------------------------------------- engine workers

    @work(thread=True, group="engine")
    def _do_set_episode(self, showid: str, episode: Any) -> None:
        with self._engine_lock:
            try:
                self.engine.set_episode(showid, episode)
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)

    @work(thread=True, group="engine")
    def _do_set_score(self, showid: str, score: Any) -> None:
        with self._engine_lock:
            try:
                self.engine.set_score(showid, score)
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)

    @work(thread=True, group="engine")
    def _do_set_status(self, showid: str, status: Any) -> None:
        with self._engine_lock:
            try:
                self.engine.set_status(showid, status)
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)
                return
        self._ui(self._rebuild_table)

    @work(thread=True, group="engine")
    def _do_play(self, show: dict, episode: str = "") -> None:
        with self._engine_lock:
            try:
                args = self.engine.play_episode(show, episode or 0)
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)
                return
        if args:
            utils.spawn_process(args)
        else:
            self._ui(self.set_status, "episode not found in library", True)

    @work(thread=True, exclusive=True, group="engine-sync")
    def _do_sync(self, then_quit: bool = False) -> None:
        with self._engine_lock:
            try:
                self.engine.list_upload()
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)
                return
        self._ui(self._reload_shows)
        self._ui(self.set_status, "changes sent")
        if then_quit:
            self._ui(self.exit)

    @work(thread=True, exclusive=True, group="engine-sync")
    def _do_retrieve(self) -> None:
        with self._engine_lock:
            try:
                self.engine.list_download()
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)
                return
        self._ui(self._reload_shows)

    @work(thread=True, group="details")
    def _do_details(self, show: dict, screen: PagerScreen, summary: Text) -> None:
        with self._engine_lock:
            try:
                data = self.engine.get_show_details(show)
            except utils.TrackmaError as error:
                self._ui(self.set_status, str(error), True)
                return
        self._ui(self._render_details, screen, summary, data)

    # ------------------------------------------------------------- shutdown

    async def _on_exit_app(self) -> None:
        if self._covers is not None:
            self._covers.shutdown()
        if self.engine is not None:
            try:
                self.engine.unload()
            except Exception:
                pass
        await super()._on_exit_app()


def main(background: str | None = None) -> int:
    manager = AccountManager()
    account = manager.get_default()
    if account is None:
        accounts = manager.get_accounts()
        first = next(iter(accounts), None)
        if first is None:
            print("No account configured. Run trackma-curses or trackma first.")
            return 1
        account = first[1]

    if background is None:
        background = terminal.background_color()

    TrackmaTui(account, background).run()
    return 0
