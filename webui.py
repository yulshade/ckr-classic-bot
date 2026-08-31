"""Small local web UI for changing run options while the bot is running.

Serves a single page (no template files, so nothing extra to bundle for
PyInstaller) that reads/writes the shared state in runtime_config.py.

The status box at the top polls /status once a second, so what the bot is
actually doing right now (the stage it last detected, or that it is looking
for one, restarting the app, or unable to reach the device) stays correct
without reloading the page -- and goes red if the bot process itself is gone.
"""
import logging
import threading
import time

from flask import Flask, render_template_string, request

import runtime_config
from config import BOOST_CHOICES, ITEM_MODE_CHOICES, ITEM_MODE_VALUES

app = Flask(__name__)
app.logger.disabled = True
# The page polls /status every second; without this every poll would print a
# request line into the bot console and bury the stage log.
logging.getLogger("werkzeug").setLevel(logging.ERROR)

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
  .status {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 14px; margin-bottom: 1.2em; border-radius: 6px; border: 1px solid;
  }
  .status .state { font-weight: bold; }
  .status.running { background: #e7f6ec; border-color: #1a7f37; color: #14532d; }
  .status.paused { background: #fdf3e3; border-color: #b06d00; color: #7a4a00; }
  .status.offline { background: #fbeaea; border-color: #b00020; color: #7a0016; }
  .status.offline form { display: none; }
  .status .stage { margin-top: 3px; font-size: 0.95em; }
  .status form { margin: 0 0 0 auto; }
  .status button { padding: 6px 18px; }
  .opt-row { display: flex; align-items: center; gap: 12px; margin: 0.6em 0; }
  .opt-row .opt-name { flex: 1; margin: 0; }
  .switch { position: relative; display: block; flex: none; width: 42px; height: 22px; margin: 0; }
  .switch input[type=checkbox] { position: absolute; opacity: 0; width: 0; height: 0; }
  .switch .slider {
    position: absolute; top: 0; right: 0; bottom: 0; left: 0;
    background: #ccc; border-radius: 22px; cursor: pointer; transition: background .15s;
  }
  .switch .slider::before {
    content: ""; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px;
    background: #fff; border-radius: 50%; transition: transform .15s;
  }
  .switch input[type=checkbox]:checked + .slider { background: #1a7f37; }
  .switch input[type=checkbox]:checked + .slider::before { transform: translateX(20px); }
  .switch input[type=checkbox]:focus-visible + .slider { outline: 2px solid #1a7f37; outline-offset: 2px; }
  .segmented { position: relative; display: inline-flex; border: 1px solid #bbb; border-radius: 6px; overflow: hidden; }
  .segmented input[type=radio] { position: absolute; opacity: 0; pointer-events: none; }
  .segmented label {
    display: block; margin: 0; padding: 5px 12px; font-size: 0.9em;
    background: #f4f4f4; color: #444; border-left: 1px solid #ddd; cursor: pointer;
  }
  .segmented label:first-of-type { border-left: none; }
  .segmented input[type=radio]:checked + label { background: #1a7f37; color: #fff; }
  .segmented input[type=radio]:focus-visible + label { outline: 2px solid #1a7f37; outline-offset: -2px; }
  .dual-slider { position: relative; height: 24px; margin: 0.6em 0 0.2em; }
  .dual-slider .track {
    position: absolute; top: 50%; left: 0; right: 0; height: 4px; margin-top: -2px;
    background: #ddd; border-radius: 2px;
  }
  .dual-slider .range-highlight {
    position: absolute; top: 50%; height: 4px; margin-top: -2px;
    background: #1a7f37; border-radius: 2px;
  }
  .dual-slider input[type=range] {
    position: absolute; left: 0; top: 0; width: 100%; height: 24px; margin: 0;
    background: none; pointer-events: none; -webkit-appearance: none; appearance: none;
  }
  .dual-slider input[type=range]::-webkit-slider-runnable-track { -webkit-appearance: none; background: transparent; }
  .dual-slider input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; pointer-events: auto; width: 16px; height: 16px; border-radius: 50%;
    background: #1a7f37; border: 2px solid #fff; box-shadow: 0 0 1px rgba(0,0,0,.5); cursor: pointer;
  }
  .dual-slider input[type=range]::-moz-range-track { background: transparent; border: none; }
  .dual-slider input[type=range]::-moz-range-thumb {
    pointer-events: auto; width: 16px; height: 16px; border-radius: 50%;
    background: #1a7f37; border: 2px solid #fff; cursor: pointer;
  }
  .slider-readout { margin: 0.2em 0 0.3em; font-variant-numeric: tabular-nums; color: #444; }
</style>
</head>
<body>
{% macro switch(field, name) -%}
<div class="opt-row">
  <span class="opt-name">{{ name }}</span>
  <label class="switch">
    <input type="checkbox" name="{{ field }}" {% if cfg[field] %}checked{% endif %}>
    <span class="slider"></span>
  </label>
</div>
{%- endmacro %}
{% macro mode_toggle(field, name) -%}
<div class="opt-row">
  <span class="opt-name">{{ name }}</span>
  <div class="segmented">
    {%- for value, label in item_mode_choices %}
    <input type="radio" id="{{ field }}_{{ value }}" name="{{ field }}" value="{{ value }}"
           {% if value == cfg[field] %}checked{% endif %}>
    <label for="{{ field }}_{{ value }}">{{ label }}</label>
    {%- endfor %}
  </div>
</div>
{%- endmacro %}
<h1>CookieRunBot Config</h1>
<div class="status {{ 'running' if cfg.running else 'paused' }}" id="status">
  <div>
    <div>Bot is <span class="state" id="status-state">{{ 'running' if cfg.running else 'paused' }}</span></div>
    <div class="stage">Stage: <span id="status-stage">{{ cfg.stage }}</span><span id="status-elapsed"></span></div>
  </div>
  <form method="post" action="/control">
    <button type="submit" name="action" id="control-button" value="{{ 'pause' if cfg.running else 'start' }}">
      {{ 'Pause' if cfg.running else 'Start' }}
    </button>
  </form>
</div>
<p>Changes apply on the bot's next loop tick &mdash; no restart needed.</p>
{% if saved %}<p class="saved">Saved.</p>{% endif %}
<form method="post" action="/update">
  <fieldset>
    <legend>Run options</legend>
    {{ mode_toggle('fast_start_mode', 'Fast Start') }}
    {{ mode_toggle('cookie_relay_mode', 'Cookie Relay') }}
    {{ switch('use_desired_random_boost', 'Desired Random Boost') }}
    <div class="opt-row">
      <label class="opt-name" for="desired_boost_name">Boost (must match the boost configured in-game)</label>
      <select name="desired_boost_name" id="desired_boost_name">
        {% for name, _ in boost_choices %}
        <option value="{{ name }}" {% if name == cfg.desired_boost_name %}selected{% endif %}>{{ name }}</option>
        {% endfor %}
      </select>
    </div>
    {{ switch('detect_relic', 'Detect Relic') }}
  </fieldset>
  <fieldset>
    <legend>Gameplay</legend>
    {{ switch('enable_auto_jump', 'Auto Jump') }}
    <div class="dual-slider">
      <div class="track"></div>
      <div class="range-highlight" id="auto_jump_range_highlight"></div>
      <input type="range" id="auto_jump_min_slider" name="auto_jump_min_interval" min="0.1" max="5.0" step="0.1"
             value="{{ cfg.auto_jump_min_interval }}" oninput="syncAutoJumpSlider('min')">
      <input type="range" id="auto_jump_max_slider" name="auto_jump_max_interval" min="0.1" max="5.0" step="0.1"
             value="{{ cfg.auto_jump_max_interval }}" oninput="syncAutoJumpSlider('max')">
    </div>
    <div class="slider-readout">
      <output id="auto_jump_min_out">{{ cfg.auto_jump_min_interval }}</output>s &ndash;
      <output id="auto_jump_max_out">{{ cfg.auto_jump_max_interval }}</output>s between taps
    </div>
  </fieldset>
  <fieldset>
    <legend>Friends &amp; lives</legend>
    {{ switch('enable_send_friend_life', 'Send Friend Life (after each session reset)') }}
    {{ switch('enable_quick_receive_send_lives', 'Quick Receive/Send Lives (periodic mailbox pass)') }}
  </fieldset>
  <fieldset>
    <legend>Device</legend>
    <label>IP: <input type="text" name="device_ip" value="{{ cfg.device_ip }}"></label>
    <label>Port: <input type="number" name="device_port" value="{{ cfg.device_port }}"></label>
  </fieldset>
  <button type="submit">Save</button>
</form>
<script>
  function syncAutoJumpSlider(moved) {
    var minEl = document.getElementById('auto_jump_min_slider');
    var maxEl = document.getElementById('auto_jump_max_slider');
    var minVal = parseFloat(minEl.value);
    var maxVal = parseFloat(maxEl.value);
    if (minVal > maxVal) {
      if (moved === 'min') { maxVal = minVal; maxEl.value = maxVal; }
      else { minVal = maxVal; minEl.value = minVal; }
    }
    document.getElementById('auto_jump_min_out').textContent = minEl.value;
    document.getElementById('auto_jump_max_out').textContent = maxEl.value;
    var lo = parseFloat(minEl.min);
    var hi = parseFloat(minEl.max);
    var leftPct = (minVal - lo) / (hi - lo) * 100;
    var rightPct = (maxVal - lo) / (hi - lo) * 100;
    var hl = document.getElementById('auto_jump_range_highlight');
    hl.style.left = leftPct + '%';
    hl.style.width = (rightPct - leftPct) + '%';
  }
  syncAutoJumpSlider('min');

  function describeElapsed(seconds) {
    var s = Math.floor(seconds);
    if (s < 1) { return ''; }
    if (s < 60) { return ' (' + s + 's)'; }
    return ' (' + Math.floor(s / 60) + 'm ' + (s % 60) + 's)';
  }
  function applyStatus(status) {
    document.getElementById('status').className = 'status ' + (status.running ? 'running' : 'paused');
    document.getElementById('status-state').textContent = status.running ? 'running' : 'paused';
    document.getElementById('status-stage').textContent = status.stage;
    document.getElementById('status-elapsed').textContent = describeElapsed(status.stage_seconds);
    var button = document.getElementById('control-button');
    button.value = status.running ? 'pause' : 'start';
    button.textContent = status.running ? 'Pause' : 'Start';
  }
  function showOffline() {
    document.getElementById('status').className = 'status offline';
    document.getElementById('status-state').textContent = 'not responding';
    document.getElementById('status-stage').textContent = 'the bot is not running';
    document.getElementById('status-elapsed').textContent = '';
  }
  function pollStatus() {
    fetch('/status', { cache: 'no-store' })
      .then(function (response) { return response.json(); })
      .then(applyStatus)
      .catch(showOffline);
  }
  pollStatus();
  setInterval(pollStatus, 1000);
</script>
</body>
</html>
"""


def _render(saved=False):
    return render_template_string(
        PAGE,
        cfg=runtime_config.get(),
        boost_choices=BOOST_CHOICES,
        item_mode_choices=ITEM_MODE_CHOICES,
        saved=saved,
    )


@app.route("/", methods=["GET"])
def index():
    return _render()


def _item_mode_from_form(form, field, current):
    """Read one three-stage item toggle, falling back to the current value."""
    try:
        mode = int(form.get(field, ""))
    except ValueError:
        return current[field]
    return mode if mode in ITEM_MODE_VALUES else current[field]


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

    try:
        auto_jump_min_interval = float(form.get("auto_jump_min_interval", ""))
    except ValueError:
        auto_jump_min_interval = current["auto_jump_min_interval"]
    try:
        auto_jump_max_interval = float(form.get("auto_jump_max_interval", ""))
    except ValueError:
        auto_jump_max_interval = current["auto_jump_max_interval"]
    if auto_jump_min_interval > auto_jump_max_interval:
        auto_jump_min_interval, auto_jump_max_interval = auto_jump_max_interval, auto_jump_min_interval

    runtime_config.update(
        fast_start_mode=_item_mode_from_form(form, "fast_start_mode", current),
        cookie_relay_mode=_item_mode_from_form(form, "cookie_relay_mode", current),
        use_desired_random_boost="use_desired_random_boost" in form,
        desired_boost_name=desired_boost_name,
        desired_boost_template=desired_boost_template,
        detect_relic="detect_relic" in form,
        enable_auto_jump="enable_auto_jump" in form,
        auto_jump_min_interval=auto_jump_min_interval,
        auto_jump_max_interval=auto_jump_max_interval,
        enable_send_friend_life="enable_send_friend_life" in form,
        enable_quick_receive_send_lives="enable_quick_receive_send_lives" in form,
        device_ip=form.get("device_ip", "").strip() or current["device_ip"],
        device_port=device_port,
    )
    return _render(saved=True)


@app.route("/status", methods=["GET"])
def status():
    """Live bot state for the status box, polled by the page every second."""
    cfg = runtime_config.get()
    return {
        "running": cfg["running"],
        "stage": cfg["stage"],
        "stage_seconds": max(0.0, time.time() - cfg["stage_since"]),
    }


@app.route("/control", methods=["POST"])
def control():
    """Start/pause the bot loop. The bot always launches paused."""
    runtime_config.update(running=request.form.get("action") == "start")
    return _render()


def start(host, port):
    """Start the config UI in a background thread and return its URL."""
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    thread.start()
    return f"http://{host}:{port}/"
