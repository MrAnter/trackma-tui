# Discord Rich Presence for Trackma: shows the show being played in mpv.
# Talks to the Discord IPC socket directly (Vesktop listens on it through arRPC).
# Pause and position come from mpv over MPRIS: the tracker's own pause flag
# stays set once the update is done, so it can't be trusted.

import json
import os
import socket
import struct
import threading
import time
import uuid

try:
    from jeepney import DBusAddress, Properties
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
except ImportError:
    open_dbus_connection = None

CONFIG = os.path.join(
    os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
    'trackma', 'discord.json')
POLL_S = 1
# A jump of the start timestamp beyond this means a seek
DRIFT_S = 3

_client_id = None
_sock = None
_current = None  # (show, episode) while something is playing
_started = 0
_wake = threading.Event()


def init(engine):
    global _client_id
    try:
        with open(CONFIG, encoding='utf-8') as f:
            _client_id = str(json.load(f)['client_id'])
    except (OSError, ValueError, KeyError) as err:
        engine.msg.warn('Discord presence disabled: no client_id in {} ({})'.format(CONFIG, err))
        return
    threading.Thread(target=_worker, name='discord-presence', daemon=True).start()


def playing(engine, show, is_playing, episode):
    global _current, _started
    _current = (show, episode) if is_playing else None
    _started = time.time()
    _wake.set()


# Discord IPC

def _socket_paths():
    base = os.environ.get('XDG_RUNTIME_DIR') or '/tmp'
    for i in range(10):
        yield os.path.join(base, 'discord-ipc-{}'.format(i))


def _send(op, payload):
    data = json.dumps(payload).encode()
    _sock.sendall(struct.pack('<II', op, len(data)) + data)
    header = _sock.recv(8)
    if len(header) < 8:
        raise ConnectionError('closed')
    _, length = struct.unpack('<II', header)
    body = b''
    while len(body) < length:
        chunk = _sock.recv(length - len(body))
        if not chunk:
            raise ConnectionError('closed')
        body += chunk
    return json.loads(body)


def _connect():
    global _sock
    for path in _socket_paths():
        if not os.path.exists(path):
            continue
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(path)
        except OSError:
            s.close()
            continue
        _sock = s
        _send(0, {'v': 1, 'client_id': _client_id})
        return True
    return False


def _set_activity(activity):
    global _sock
    payload = {
        'cmd': 'SET_ACTIVITY',
        'args': {'pid': os.getpid(), 'activity': activity},
        'nonce': str(uuid.uuid4()),
    }
    # One retry: Discord may have been restarted since the last call
    for _ in range(2):
        try:
            if _sock is None and not _connect():
                return False
            _send(1, payload)
            return True
        except (OSError, ConnectionError, ValueError):
            if _sock is not None:
                _sock.close()
            _sock = None
    return False


# mpv over MPRIS

def _mpv_status(bus):
    """(paused, position_s, length_s) of the mpv instance, or None."""
    if bus is None:
        return None
    try:
        names = bus.send_and_get_reply(message_bus.ListNames()).body[0]
        players = []
        for name in names:
            if not name.startswith('org.mpris.MediaPlayer2.mpv'):
                continue
            addr = DBusAddress('/org/mpris/MediaPlayer2', bus_name=name,
                               interface='org.mpris.MediaPlayer2.Player')
            props = bus.send_and_get_reply(Properties(addr).get_all(), timeout=2).body[0]
            players.append({k: v[1] for k, v in props.items()})
    except Exception:
        return None
    if not players:
        return None
    # With several mpv windows open, the one playing wins
    p = next((p for p in players if p.get('PlaybackStatus') == 'Playing'), players[0])
    length = p.get('Metadata', {}).get('mpris:length', (None, 0))[1]
    return (p.get('PlaybackStatus') != 'Playing',
            p.get('Position', 0) / 1e6,
            length / 1e6 if length else None)


def _open_bus():
    if open_dbus_connection is None:
        return None
    try:
        return open_dbus_connection(bus='SESSION')
    except Exception:
        return None


def _activity(show, episode, status, paused_at):
    total = show.get('total') or '?'
    activity = {'type': 3, 'details': show['title'][:128],  # 3: Watching
                'state': 'Episode {} of {}'.format(episode, total)}
    if status is None:
        activity['timestamps'] = {'start': int(_started)}
    else:
        paused, position, length = status
        if paused:
            # Discord can't freeze a running bar, but it stops one whose end
            # is past: end at the pause, so the counter holds the position
            end = int(paused_at) - 1
            activity['timestamps'] = {'start': end - int(position), 'end': end}
        else:
            start = time.time() - position
            activity['timestamps'] = {'start': int(start)}
            if length:
                activity['timestamps']['end'] = int(start + length)
    assets = {}
    if show.get('image'):
        assets.update(large_image=show['image'], large_text=show['title'][:128])
    if status is not None:
        # Keys of the Art Assets uploaded to the Discord application
        if status[0]:
            assets.update(small_image='pause', small_text='Paused')
        else:
            assets.update(small_image='play', small_text='Playing')
    if assets:
        activity['assets'] = assets
    if show.get('url'):
        activity['buttons'] = [{'label': 'AniList', 'url': show['url']}]
    return activity


def _changed(old, new):
    if old is None or old.keys() != new.keys():
        return True
    for key in new:
        if key != 'timestamps' and old[key] != new[key]:
            return True
    if 'timestamps' in new:
        if old['timestamps'].keys() != new['timestamps'].keys():
            return True
        return abs(new['timestamps']['start'] - old['timestamps']['start']) > DRIFT_S
    return False


def _worker():
    bus = _open_bus()
    sent = None
    paused_at = paused_pos = None
    while True:
        _wake.wait(POLL_S)
        _wake.clear()
        current = _current
        if current is None:
            if sent is not None and _set_activity(None):
                sent = None
            continue
        if bus is None:
            bus = _open_bus()
        status = _mpv_status(bus)
        if status is None and bus is not None:
            # No mpv on the bus, or a dead connection: reopen next round
            bus.close()
            bus = None
        if status and status[0]:
            if paused_pos != status[1]:
                paused_at, paused_pos = time.time(), status[1]
        else:
            paused_pos = None
        activity = _activity(*current, status, paused_at)
        if (sent is None or current != sent[0] or _changed(sent[1], activity)) \
                and _set_activity(activity):
            sent = (current, activity)
