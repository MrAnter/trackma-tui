# trackma-tui

A terminal interface for [Trackma](https://github.com/z411/trackma), built with
[Textual](https://textual.textualize.io/): **real cover art in the terminal**
and a palette that survives a dark background.

Not a fork. It sits on `trackma.engine` like the interfaces that ship with
Trackma (`cli`, `curses`, `gtk`, `qt`), so accounts, cache and the sync queue
are shared. What you do here shows up in the GUIs, and the other way round.

## Why

Two things the stock `trackma-curses` doesn't do:

- **Readability.** Its palette is built for a light terminal — `dark blue` on a
  transparent background, which disappears on a dark theme. Everything here sits
  between 65% and 75% lightness, and is configurable.
- **Cover art.** GTK and Qt show covers; the terminal didn't. Ghostty, Kitty and
  friends speak real graphics protocols, so these are pixels, not ASCII art.

## Requirements

- `trackma` >= 0.10, with an account already set up
- `textual` >= 1.0
- `textual-image` — optional, but it's the point: no covers without it
- a terminal speaking the Kitty graphics protocol or Sixel: ghostty, kitty,
  WezTerm, foot. Without one it falls back to coloured half-blocks.

On Arch:

```bash
sudo pacman -S trackma python-textual
yay -S python-textual-image
```

## Use

```bash
trackma-tui
```

Bare on purpose: no title bar, no tabs, no buttons. A filter line, the list, the
cover, a status line, a key legend, and a `:` command line that shows up only
while you're typing into it.

The mouse works throughout: click a filter, click a row, click a column header
to sort by it (again to reverse), click an entry in the legend, scroll with the
wheel.

### Keys

| Key | Action |
|---|---|
| `j` `k` / arrows | move through the list |
| `g` `G` | first / last show |
| `shift+left` `shift+right` | previous / next filter |
| `/` | search titles (`Esc` cancels) |
| `:` | command line |
| `+` `-` | one episode more / less |
| `u` `z` `t` | episode, score, status — open the command line pre-filled |
| `p` | play the next episode |
| `i` | details page |
| `s` | send queued changes |
| `o` `O` | open the folder / the show's web page |
| `?` | keys and commands |
| `q` | quit |

### Commands

| Command | Does |
|---|---|
| `:+1` `:-1` `:+3` | add or remove episodes |
| `:ep 13` | set a specific episode |
| `:score 8.5` | score |
| `:status watching` | change status |
| `:filter all` | filter by status |
| `:sort next` | sort: title, watched, available, aired, local, next, score, updated; repeating the same one reverses it |
| `:play` `:play 5` | play |
| `:info` `:open` `:folder` | details, web page, folder |
| `:w` | send queued changes |
| `:fetch` | re-download the list |
| `:q` `:q!` `:wq` | quit, quit discarding, send and quit |

### Nothing is saved on its own

Trackma keeps changes in a local queue until you sync. The status line always
says how many are waiting, changed rows turn violet, and `:q` **refuses to
quit** while the queue isn't empty — as vim does. `:q!` leaves anyway, `:wq`
sends first.

### The tracker

The right end of the status line follows Trackma's tracker: `listening` while
nothing plays, then the show, the episode and the countdown to the update
(`update in 1:23`, `[paused]` when the player is paused, `when the player
closes` once the countdown is over and Trackma is set to wait for that). A
file it can't match shows up as a warning there too.

### The availability bar

The `available` column answers "how far am I, and what is there to watch?" in
one line:

| | |
|---|---|
| blue | watched |
| green | downloaded, not watched yet |
| amber | aired, not downloaded |
| dim dots | not aired yet |

Shows with no episode count (a long runner like One Piece) are scaled against
the furthest episode known.

## Configuration

`~/.config/trackma/ui-textual.json` is written on first run, next to Trackma's
other `ui-*.json` files:

```json
{
    "colors": {
        "airing": "#6cb6ff",
        "notaired": "#e3b341",
        "neweps": "#7ee787",
        "queued": "#d2a8ff",
        "playing": "#ff9e64",
        "normal": "#c9d1d9",
        "dim": "#8b949e",
        "cursor": "#1f2937"
    },
    "columns": ["title", "progress", "bar", "next", "score"],
    "transparent_background": true,
    "image_protocol": "auto",
    "default_filter": "CURRENT",
    "default_sort": "title"
}
```

`columns` takes any of `title`, `progress`, `bar`, `aired`, `library`, `next`,
`score`, `status`, `type`, in the order you want them. `title` is always shown
and takes whatever width the others leave.

`image_protocol` accepts `auto`, `tgp` (Kitty graphics), `sixel`, `halfcell`,
`unicode`. Only needed when auto-detection gets it wrong.

`transparent_background` asks the terminal for its own background colour over
OSC 11 and paints that, so the app sits flush on whatever theme is in use. Set
it to `false` to use the palette's `background` instead.

## Covers and the cache

The engine doesn't download images: it exposes a URL and leaves the rest to each
interface. Here the download happens on a worker thread, and files land in
`~/.cache/trackma/` under **the same name GTK and Qt use** for the full-size
cover, `<api>_<mediatype>_f_<id>.jpg`, so nobody fetches the same cover twice.

They are stored at full resolution. The GUIs thumbnail before saving — which is
why their cached covers look soft in a terminal that draws real pixels — and
since they scale whatever is on disk, they benefit too.

## Discord Rich Presence

`hooks/discord_presence.py` shows what you're watching on Discord: "Watching
<app name>", the show, `Episode 3 of 12`, the cover, a progress bar and an
AniList button. Pausing stops the counter where the video is, seeking moves
the bar, and it clears when the player closes.

Pause and position are read from mpv over MPRIS (`mpv-mpris`), whatever
tracker Trackma uses. Without it the presence still works, with elapsed time
instead of the bar.

It's a plain Trackma hook, so it works with **any** interface (Qt, GTK, curses,
this one), not just trackma-tui. It talks to the Discord IPC socket directly:
no extra dependencies, and it works with Vesktop too.

1. Create an application at <https://discord.com/developers/applications>. Its
   name is what Discord shows after "Watching", so call it `Anime` or similar.
2. Put its Application ID in `~/.config/trackma/discord.json`, which stays out
   of the repository:

   ```json
   {
       "client_id": "123456789012345678"
   }
   ```

3. Optional: under *Rich Presence → Art Assets* upload `hooks/assets/play.png`
   and `pause.png`, named `play` and `pause`. They show as a small badge on the
   cover while the video plays or is paused.
4. Link the hook where Trackma looks for them (`use_hooks` must be `true` in
   Trackma's `config.json`, the default):

   ```bash
   mkdir -p ~/.config/trackma/hooks
   ln -s "$PWD/hooks/discord_presence.py" ~/.config/trackma/hooks/
   ```

Without `discord.json` the hook logs a warning and does nothing.

## Licence

GPL-3.0-or-later, like Trackma.
