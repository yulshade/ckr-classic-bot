"""Thread-safe run options shared between the bot loop and the config UI.

Replaces the old prompt_user_options() flow: instead of collecting these
once at startup, they live here and can be changed at any time via the
web UI (webui.py) while bot.py's main loop reads them each iteration.
"""
import threading

from config import BOOST_CHOICES, DEVICE_IP, DEVICE_PORT

_lock = threading.Lock()

_state = {
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
    # internal: mirrors bot.py's detection_group, not user-editable via the UI form
    "in_game": False,
}


def get():
    """Return a snapshot dict of the current run options."""
    with _lock:
        return dict(_state)


def get_device():
    """Return (device_ip, device_port) for the current call to adb."""
    with _lock:
        return _state["device_ip"], _state["device_port"]


def update(**kwargs):
    """Merge the given fields into the shared state."""
    with _lock:
        _state.update(kwargs)
