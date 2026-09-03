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
import time

from config import (
    AUTO_JUMP_INTERVAL,
    BOOST_CHOICES,
    DEVICE_IP,
    DEVICE_PORT,
    ITEM_MODE_BUY_AND_USE,
    ITEM_MODE_OFF,
    ITEM_MODE_VALUES,
)

_lock = threading.Lock()

_DEFAULTS = {
    "running": False,
    "device_ip": DEVICE_IP,
    "device_port": DEVICE_PORT,
    "fast_start_mode": ITEM_MODE_OFF,
    "cookie_relay_mode": ITEM_MODE_OFF,
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
# detection_group and stage/stage_since describe what the loop is doing right
# now, none of them user-editable via the UI form), the session tally
# (runs_completed/active_seconds/running_since count this app run only), the
# start/pause flag (the app always launches paused, waiting for Start in the
# web UI) and desired_boost_template (derived from desired_boost_name via
# config.BOOST_CHOICES on load, since it isn't JSON-safe).
_NON_PERSISTED_FIELDS = {
    "running", "in_game", "stage", "stage_since", "desired_boost_template",
    "runs_completed", "active_seconds", "running_since",
}

_base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
PERSIST_PATH = os.path.join(_base_dir, "runtime_config.json")


def _boost_template_for(name):
    for boost_name, template in BOOST_CHOICES:
        if boost_name == name:
            return template
    return _DEFAULTS["desired_boost_template"]


def _item_mode(value, fallback):
    """Coerce a persisted value into a valid ITEM_MODE_* constant."""
    try:
        mode = int(value)
    except (TypeError, ValueError):
        return fallback
    return mode if mode in ITEM_MODE_VALUES else fallback


def _migrate_legacy(saved, state):
    """Carry over the pre-three-stage booleans from an older config file.

    Fast Start and Cookie Relay used to be a single on/off flag that gated both
    buying and using the item, which is exactly what ITEM_MODE_BUY_AND_USE now
    means -- so an old True becomes that mode rather than resetting to Off.
    """
    for legacy_key, key in (("use_fast_start", "fast_start_mode"), ("use_cookie_relay", "cookie_relay_mode")):
        if key not in saved and legacy_key in saved:
            state[key] = ITEM_MODE_BUY_AND_USE if saved[legacy_key] else ITEM_MODE_OFF


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
    _migrate_legacy(saved, state)
    for key in ("fast_start_mode", "cookie_relay_mode"):
        state[key] = _item_mode(state[key], _DEFAULTS[key])
    state["desired_boost_template"] = _boost_template_for(state["desired_boost_name"])
    return state


_state = _load_persisted()
_state["in_game"] = False
# Always start paused, regardless of anything left in the persisted file.
_state["running"] = False
# The loop overwrites this within a quarter second, but the page can be
# opened before the loop's first tick, so seed the same words it would.
_state["stage"] = "Nothing tapped yet"
_state["stage_since"] = time.time()
# What the UI reports about this app run: runs finished since the first Start,
# and the time spent running. Time spent paused is not counted -- the bot taps
# nothing then, and the rest of the loop's timers already skip it the same way.
# active_seconds banks the stretches that have ended; running_since holds the
# start of the one still open, so a running total stays live without the loop
# writing a counter on every tick.
_state["runs_completed"] = 0
_state["active_seconds"] = 0.0
_state["running_since"] = None


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


def set_stage(stage):
    """Record what the bot loop is doing right now, for display in the web UI.

    Called from bot.py on every loop iteration, so the timestamp is only
    refreshed when the text actually changes — the UI shows how long the bot
    has been on this stage, which is the tell that it is stuck rather than
    progressing.
    """
    with _lock:
        if _state["stage"] != stage:
            _state["stage"] = stage
            _state["stage_since"] = time.time()


def note_run_complete():
    """Count one finished run and return the session total.

    Called by bot.py when a run's results screen is first detected, which is
    the only moment the bot knows a run reached the end.
    """
    with _lock:
        _state["runs_completed"] += 1
        return _state["runs_completed"]


def session_totals():
    """(runs completed, seconds spent running) since the first Start."""
    with _lock:
        seconds = _state["active_seconds"]
        if _state["running_since"] is not None:
            seconds += time.time() - _state["running_since"]
        return _state["runs_completed"], seconds


def _mark_running(value):
    """Open or close the current running stretch. Caller must hold _lock.

    running_since is the record of whether one is open, so repeating the state
    the bot is already in (Start pressed twice, a stop path that also sets
    running=False) neither restarts the clock nor banks the same seconds twice.
    """
    if bool(value) == (_state["running_since"] is not None):
        return
    if value:
        _state["running_since"] = time.time()
    else:
        _state["active_seconds"] += time.time() - _state["running_since"]
        _state["running_since"] = None


def update(**kwargs):
    """Merge the given fields into the shared state.

    Persists to disk only when a persisted field actually changed — bot.py's
    main loop calls this every ~0.25s with the internal in_game flag, and that
    alone shouldn't trigger a disk write.
    """
    with _lock:
        _state.update(kwargs)
        if "running" in kwargs:
            _mark_running(kwargs["running"])
        if any(k not in _NON_PERSISTED_FIELDS for k in kwargs):
            _save_persisted()
