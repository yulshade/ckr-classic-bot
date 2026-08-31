"""Thread-safe run options shared between the bot loop and the config UI.

Replaces the old prompt_user_options() flow: instead of collecting these
once at startup, they live here and can be changed at any time via the
web UI (webui.py) while bot.py's main loop reads them each iteration.

User-editable fields are persisted to a JSON file (see PERSIST_PATH) on
every update() call and reloaded here at import time, so the choices made
in the web UI survive an app restart instead of resetting to defaults.
"""
import json
import os
import sys
import threading

from config import AUTO_JUMP_INTERVAL, BOOST_CHOICES, DEVICE_IP, DEVICE_PORT

_lock = threading.Lock()

_DEFAULTS = {
    "running": False,
    "device_ip": DEVICE_IP,
    "device_port": DEVICE_PORT,
    "use_fast_start": False,
    "use_cookie_relay": False,
    "use_desired_random_boost": False,
    "desired_boost_name": BOOST_CHOICES[0][0],
    "desired_boost_template": BOOST_CHOICES[0][1],
    "detect_relic": False,
    "enable_send_friend_life": True,
    "enable_quick_receive_send_lives": True,
    "enable_auto_jump": False,
    "auto_jump_min_interval": AUTO_JUMP_INTERVAL[0],
    "auto_jump_max_interval": AUTO_JUMP_INTERVAL[1],
}

# Fields not persisted to disk: internal state (in_game mirrors bot.py's
# detection_group, not user-editable via the UI form), the start/pause flag
# (the app always launches paused, waiting for Start in the web UI) and
# desired_boost_template (derived from desired_boost_name via
# config.BOOST_CHOICES on load, since it isn't JSON-safe).
_NON_PERSISTED_FIELDS = {"running", "in_game", "desired_boost_template"}

_base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
PERSIST_PATH = os.path.join(_base_dir, "runtime_config.json")


def _boost_template_for(name):
    for boost_name, template in BOOST_CHOICES:
        if boost_name == name:
            return template
    return _DEFAULTS["desired_boost_template"]


def _load_persisted():
    state = dict(_DEFAULTS)
    try:
        with open(PERSIST_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    for key, value in saved.items():
        if key in _DEFAULTS:
            state[key] = value
    state["desired_boost_template"] = _boost_template_for(state["desired_boost_name"])
    return state


_state = _load_persisted()
_state["in_game"] = False
# Always start paused, regardless of anything left in the persisted file.
_state["running"] = False


def _save_persisted():
    """Write the persistable fields to PERSIST_PATH. Caller must hold _lock."""
    to_save = {k: v for k, v in _state.items() if k not in _NON_PERSISTED_FIELDS}
    try:
        with open(PERSIST_PATH, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except OSError as e:
        print(f"⚠️ Failed to save config to {PERSIST_PATH}: {e}")


def get():
    """Return a snapshot dict of the current run options."""
    with _lock:
        return dict(_state)


def get_device():
    """Return (device_ip, device_port) for the current call to adb."""
    with _lock:
        return _state["device_ip"], _state["device_port"]


def update(**kwargs):
    """Merge the given fields into the shared state.

    Persists to disk only when a persisted field actually changed — bot.py's
    main loop calls this every ~0.25s with the internal in_game flag, and that
    alone shouldn't trigger a disk write.
    """
    with _lock:
        _state.update(kwargs)
        if any(k not in _NON_PERSISTED_FIELDS for k in kwargs):
            _save_persisted()
