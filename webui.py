"""Small local web UI for changing run options while the bot is running.

Serves a single page (no template files, so nothing extra to bundle for
PyInstaller) that reads/writes the shared state in runtime_config.py.
"""
import threading

from flask import Flask, render_template_string, request

import runtime_config
from config import BOOST_CHOICES

app = Flask(__name__)
app.logger.disabled = True

PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>CookieRunBot Config</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 480px; margin: 40px auto; color: #222; }
  h1 { font-size: 1.3em; }
  fieldset { margin-bottom: 1.2em; border: 1px solid #ccc; border-radius: 6px; }
  label { display: block; margin: 0.5em 0; }
  select, input[type=text], input[type=number] { padding: 4px; }
  button { padding: 8px 20px; font-size: 1em; }
  .saved { color: #1a7f37; font-weight: bold; }
</style>
</head>
<body>
<h1>CookieRunBot Config</h1>
<p>Changes apply on the bot's next loop tick &mdash; no restart needed.</p>
{% if saved %}<p class="saved">Saved.</p>{% endif %}
<form method="post" action="/update">
  <fieldset>
    <legend>Run options</legend>
    <label><input type="checkbox" name="use_fast_start" {% if cfg.use_fast_start %}checked{% endif %}> Fast Start (buy + use)</label>
    <label><input type="checkbox" name="use_cookie_relay" {% if cfg.use_cookie_relay %}checked{% endif %}> Cookie Relay (buy + use)</label>
    <label>
      <input type="checkbox" name="use_desired_random_boost" {% if cfg.use_desired_random_boost %}checked{% endif %}>
      Desired Random Boost (buy + use)
    </label>
    <label>
      Boost (must match the boost configured in-game):
      <select name="desired_boost_name">
        {% for name, _ in boost_choices %}
        <option value="{{ name }}" {% if name == cfg.desired_boost_name %}selected{% endif %}>{{ name }}</option>
        {% endfor %}
      </select>
    </label>
    <label><input type="checkbox" name="detect_relic" {% if cfg.detect_relic %}checked{% endif %}> Detect Relic (open + claim)</label>
  </fieldset>
  <fieldset>
    <legend>Gameplay</legend>
    <label>
      <input type="checkbox" name="enable_auto_jump" {% if cfg.enable_auto_jump %}checked{% endif %}>
      Auto Jump (taps the fixed Jump button, 300ms-1s random interval, while a run is in progress)
    </label>
  </fieldset>
  <fieldset>
    <legend>Friends &amp; lives</legend>
    <label><input type="checkbox" name="enable_send_friend_life" {% if cfg.enable_send_friend_life %}checked{% endif %}> Send Friend Life (after each session reset)</label>
    <label><input type="checkbox" name="enable_quick_receive_send_lives" {% if cfg.enable_quick_receive_send_lives %}checked{% endif %}> Quick Receive/Send Lives (periodic mailbox pass)</label>
  </fieldset>
  <fieldset>
    <legend>Device</legend>
    <label>IP: <input type="text" name="device_ip" value="{{ cfg.device_ip }}"></label>
    <label>Port: <input type="number" name="device_port" value="{{ cfg.device_port }}"></label>
  </fieldset>
  <button type="submit">Save</button>
</form>
</body>
</html>
"""


def _render(saved=False):
    return render_template_string(PAGE, cfg=runtime_config.get(), boost_choices=BOOST_CHOICES, saved=saved)


@app.route("/", methods=["GET"])
def index():
    return _render()


@app.route("/update", methods=["POST"])
def update():
    form = request.form
    current = runtime_config.get()

    desired_boost_name = form.get("desired_boost_name", current["desired_boost_name"])
    desired_boost_template = current["desired_boost_template"]
    for name, template in BOOST_CHOICES:
        if name == desired_boost_name:
            desired_boost_template = template
            break

    try:
        device_port = int(form.get("device_port", "").strip())
    except ValueError:
        device_port = current["device_port"]

    runtime_config.update(
        use_fast_start="use_fast_start" in form,
        use_cookie_relay="use_cookie_relay" in form,
        use_desired_random_boost="use_desired_random_boost" in form,
        desired_boost_name=desired_boost_name,
        desired_boost_template=desired_boost_template,
        detect_relic="detect_relic" in form,
        enable_auto_jump="enable_auto_jump" in form,
        enable_send_friend_life="enable_send_friend_life" in form,
        enable_quick_receive_send_lives="enable_quick_receive_send_lives" in form,
        device_ip=form.get("device_ip", "").strip() or current["device_ip"],
        device_port=device_port,
    )
    return _render(saved=True)


def start(host, port):
    """Start the config UI in a background thread and return its URL."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    thread.start()
    return f"http://{host}:{port}/"
