import random
import threading
import time

import runtime_config
import webui
from adb import device_capture_screen, device_connect, device_reset_app, device_tap
from actions import (
    accept_congratulations,
    accept_daily_checkin,
    accept_daily_checkin_boost_set,
    accept_daily_new,
    accept_daily_treasure,
    accept_enter_league,
    accept_league_results,
    accept_level_up,
    accept_mystery_box,
    accept_overtake_break_score,
    accept_previous_rank_results,
    accept_relic_claim,
    accept_too_many_treasures,
    close_announcement_dialog,
    complete_finish,
    handle_anti_bot,
    handle_inactive,
    handle_quick_receive_and_send_lives,
    handle_send_friend_life,
    open_relic_complete,
    play_game,
    purchase_cookie_relay,
    purchase_desired_random_boost,
    purchase_fast_start,
    run_auto_jump,
    spam_fast_start,
    start_game,
    using_cookie_relay,
    using_fast_start,
)
from config import (
    DETECTION_ALWAYS_STAGES,
    DETECTION_GROUPS,
    DETECTION_RECOVERY_SCAN_INTERVAL,
    ITEM_MODE_BUY_AND_USE,
    ITEM_MODE_OFF,
    RESUME_STAGE_CHECK_TIMEOUT,
    SESSION_RESET_INTERVAL,
    UNKNOWN_STAGE_REPORT_DELAY,
    WEBUI_HOST,
    WEBUI_PORT,
)
from detection import detect_stage, load_templates
from debug import save_debug_screen


def get_detection_stage_names(group_name, exclude=None):
    stage_names = []
    # For non-in-game groups, always stages have higher priority
    if group_name != "IN_GAME":
        for stage_name in DETECTION_ALWAYS_STAGES:
            if stage_name not in stage_names:
                stage_names.append(stage_name)
    # Add stages from the specified detection group
    for stage_name in DETECTION_GROUPS[group_name]:
        if stage_name not in stage_names:
            stage_names.append(stage_name)
    # For in-game, always stages are appended last (original behavior)
    if group_name == "IN_GAME":
        for stage_name in DETECTION_ALWAYS_STAGES:
            if stage_name not in stage_names:
                stage_names.append(stage_name)
    if exclude:
        stage_names = [s for s in stage_names if s not in exclude]
    return stage_names


# Reverse of DETECTION_GROUPS: which group a detected stage puts the bot in.
# Used to re-establish the state machine after a pause (first group wins, so
# MAINMENU — listed in both PRE_GAME and POST_GAME — maps to PRE_GAME).
STAGE_DETECTION_GROUP = {}
for _group_name in ("PRE_GAME", "IN_GAME", "POST_GAME"):
    for _stage_name in DETECTION_GROUPS[_group_name]:
        STAGE_DETECTION_GROUP.setdefault(_stage_name, _group_name)
for _stage_name in DETECTION_ALWAYS_STAGES:
    STAGE_DETECTION_GROUP.setdefault(_stage_name, "PRE_GAME")


# -------------------
# MAIN LOOP
# -------------------
def main():
    try:
        print("🚀 CookieRun Classic Bot Started")
        print("⚠️ Screen must be 1280x720 resolution for the bot to work properly.")

        device_ip, device_port = runtime_config.get_device()
        print(f"📱 Connecting to device at {device_ip}:{device_port}...")
        device_connect(device_ip, device_port)
        load_templates()

        # * for debugging *
        # device_screen = device_capture_screen(device_ip, device_port)
        # save_debug_screen(device_screen)

        url = webui.start(WEBUI_HOST, WEBUI_PORT)
        print(f"🖥️ Config UI running at {url} — open it in a browser to change run options anytime, no restart needed.")

        threading.Thread(target=run_auto_jump, daemon=True).start()

        last_stage = None
        is_first_game = True
        detection_group = "PRE_GAME"
        last_detected_time = time.time()
        last_known_stage_time = time.time()
        device_unreachable = False
        session_start_time = time.time()
        session_reset_interval = random.uniform(*SESSION_RESET_INTERVAL)
        last_lives_time = time.time()
        lives_interval = random.uniform(25 * 60, 35 * 60)
        pending_send_friend_life = False
        paused_since = None
        has_run = False
        resync_pending = False
        resync_started_time = None

        while True:
            options = runtime_config.get()
            if not options["running"]:
                if paused_since is None:
                    paused_since = time.time()
                    runtime_config.update(in_game=False)
                    last_stage = None
                    print("⏸️ Paused — press Start in the config UI to "
                          + ("resume." if has_run else "begin."))
                runtime_config.set_stage(
                    "Not touching the device" if has_run
                    else "Nothing tapped yet")
                time.sleep(0.25)
                continue
            if paused_since is not None:
                paused_for = time.time() - paused_since
                paused_since = None
                # Don't let time spent paused count toward the background timers.
                session_start_time += paused_for
                last_lives_time += paused_for
                last_detected_time += paused_for
                print(f"⏩ Resumed after {paused_for:.0f}s paused." if has_run else "⏩ Started.")
                has_run = True
                # The screen may have moved on while paused, so nothing acts
                # (auto jump included) until a fresh scan says where we are.
                resync_pending = True
                resync_started_time = time.time()
                last_stage = None
                last_known_stage_time = time.time()
                runtime_config.set_stage("Checking the current stage...")
                print("🔍 Checking the current stage before resuming actions...")
            runtime_config.update(in_game=(not resync_pending and detection_group == "IN_GAME"))
            if (options["device_ip"], options["device_port"]) != (device_ip, device_port):
                device_ip, device_port = options["device_ip"], options["device_port"]
                print(f"📱 Device target changed — reconnecting to {device_ip}:{device_port}...")
                device_connect(device_ip, device_port)
            relic_exclude = None if options["detect_relic"] else {"RELIC_COMPLETE", "RELIC_CLAIM"}

            try:
                device_screen = device_capture_screen(device_ip, device_port)
            except Exception as e:
                device_screen, capture_error = None, e
            else:
                capture_error = None if device_screen is not None else "empty screen capture"
            if device_screen is None:
                # The device went away (emulator closed, adb dropped, cable
                # unplugged). Report that instead of crashing out of the loop
                # or leaving the last detected stage on display forever.
                if not device_unreachable:
                    device_unreachable = True
                    print(f"📵 Lost the device at {device_ip}:{device_port} ({capture_error}) — retrying...")
                runtime_config.update(in_game=False)
                runtime_config.set_stage("Device not responding — reconnecting...")
                last_stage = None
                try:
                    device_connect(device_ip, device_port)
                except Exception:
                    pass
                time.sleep(2)
                continue
            if device_unreachable:
                device_unreachable = False
                print(f"📶 Device at {device_ip}:{device_port} is back — rechecking the stage...")
                # The screen almost certainly moved on while it was gone, so
                # re-derive the detection group the way a resume does.
                resync_pending = True
                resync_started_time = time.time()
                last_known_stage_time = time.time()
                runtime_config.set_stage("Checking the current stage...")
            if resync_pending:
                # Full unfiltered scan — the pre-pause detection group can't be
                # trusted. Keep idling (no actions, no auto jump) until a stage
                # is recognized, e.g. resuming mid-run waits for GAME_COMPLETE.
                stage = detect_stage(device_screen, exclude=relic_exclude)
                if stage is None:
                    resync_elapsed = time.time() - resync_started_time
                    if resync_elapsed >= RESUME_STAGE_CHECK_TIMEOUT:
                        # Unknown screen for too long — restart the app to get
                        # back to a known one, then check the stage again.
                        print(f"❓ No stage recognized {resync_elapsed:.0f}s after resuming — restarting app...")
                        runtime_config.set_stage("Restarting the game...")
                        device_reset_app(device_ip, device_port)
                        time.sleep(5)
                        close_announcement_dialog()
                        session_start_time = time.time()
                        session_reset_interval = random.uniform(*SESSION_RESET_INTERVAL)
                        last_lives_time = time.time()
                        lives_interval = random.uniform(25 * 60, 35 * 60)
                        detection_group = "PRE_GAME"
                        is_first_game = True
                        resync_started_time = time.time()
                        runtime_config.set_stage("Checking the current stage...")
                        print("🔍 Checking the current stage after the restart...")
                    time.sleep(0.25)
                    continue
                detection_group = STAGE_DETECTION_GROUP.get(stage, "PRE_GAME")
                print(f"✅ Stage check: {stage} — resuming in detection group {detection_group}.")
                resync_pending = False
                resync_started_time = None
                last_detected_time = time.time()
            else:
                stage = detect_stage(device_screen, get_detection_stage_names(detection_group, exclude=relic_exclude))
                if stage is None:
                    if time.time() - last_detected_time >= DETECTION_RECOVERY_SCAN_INTERVAL[detection_group]:
                        stage = detect_stage(device_screen, exclude=relic_exclude)
                        last_detected_time = time.time()
                else:
                    last_detected_time = time.time()

            if stage is not None:
                last_known_stage_time = time.time()
                runtime_config.set_stage(f"Stage: {stage}")
            elif time.time() - last_known_stage_time >= UNKNOWN_STAGE_REPORT_DELAY:
                # Nothing has matched for a while: the game was closed or
                # crashed, or it is on a screen there is no template for.
                runtime_config.set_stage("No stage recognized — still looking...")

            if stage == last_stage:
                time.sleep(0.1)
                continue

            last_stage = stage

            if stage == "MAINMENU":
                print("🎮 Detected Stage: MAINMENU")
                # Wait screen refresh
                print("⏳ Waiting 5 seconds for screen refresh...")
                time.sleep(5)
                if pending_send_friend_life:
                    if options["enable_send_friend_life"]:
                        print("💌 Sending friend lives after app reset...")
                        runtime_config.set_stage("Sending friend lives...")
                        handle_send_friend_life()
                    else:
                        print("💌 Send Friend Life disabled — skipping post-reset friend life pass.")
                    pending_send_friend_life = False
                    last_lives_time = time.time()
                    last_stage = None
                    continue
                elapsed = time.time() - session_start_time
                if elapsed >= session_reset_interval:
                    print(f"🔄 Session reset triggered after {elapsed / 3600:.2f}h — restarting app...")
                    runtime_config.set_stage("Restarting the game (session reset)...")
                    device_reset_app(device_ip, device_port)
                    time.sleep(5)
                    close_announcement_dialog()
                    pending_send_friend_life = True
                    session_start_time = time.time()
                    session_reset_interval = random.uniform(*SESSION_RESET_INTERVAL)
                    last_lives_time = time.time()
                    lives_interval = random.uniform(25 * 60, 35 * 60)
                    detection_group = "PRE_GAME"
                    last_stage = None
                    is_first_game = True
                    continue
                lives_elapsed = time.time() - last_lives_time
                if lives_elapsed >= lives_interval:
                    if options["enable_quick_receive_send_lives"]:
                        print(f"💌 ~30 min passed ({lives_elapsed / 60:.1f} min) — receiving and sending lives...")
                        runtime_config.set_stage("Receiving and sending lives...")
                        handle_quick_receive_and_send_lives()
                    else:
                        print(f"💌 ~30 min passed ({lives_elapsed / 60:.1f} min) — Quick Receive/Send Lives disabled, skipping.")
                    last_lives_time = time.time()
                    lives_interval = random.uniform(25 * 60, 35 * 60)
                    last_stage = None
                    continue
                if detection_group == "POST_GAME":
                    detection_group = "PRE_GAME"
                    last_stage = None
                    continue
                if not is_first_game:
                    delay = random.uniform(5, 15)
                    print(f"⏳ Waiting for {delay:.2f} seconds before starting the next game...")
                    time.sleep(delay)
                is_first_game = False
                start_game()
                detection_group = "PRE_GAME"
            elif stage == "PURCHASE_ITEM":
                print("🛒 Detected Stage: PURCHASE_ITEM")
                if options["fast_start_mode"] == ITEM_MODE_BUY_AND_USE:
                    purchase_fast_start()
                if options["cookie_relay_mode"] == ITEM_MODE_BUY_AND_USE:
                    purchase_cookie_relay()
                if options["use_desired_random_boost"]:
                    purchase_desired_random_boost(options["desired_boost_template"], options["desired_boost_name"])
                play_game()
                if options["fast_start_mode"] != ITEM_MODE_OFF:
                    runtime_config.set_stage("Using Fast Start...")
                    spam_fast_start()
                detection_group = "IN_GAME"
                time.sleep(0.2)
                last_stage = None
            elif stage == "GAME_START":
                print("🏁 Detected Stage: GAME_START")
                if options["fast_start_mode"] != ITEM_MODE_OFF:
                    using_fast_start()
                detection_group = "IN_GAME"
            elif stage == "GAME_RELAY":
                print("🔄 Detected Stage: GAME_RELAY")
                if options["cookie_relay_mode"] != ITEM_MODE_OFF:
                    using_cookie_relay()
                detection_group = "IN_GAME"
            elif stage == "GAME_COMPLETE":
                print("✅ Detected Stage: GAME_COMPLETE")
                complete_finish()
                detection_group = "POST_GAME"
            elif stage == "MYSTERY_BOX":
                print("🎁 Detected Stage: MYSTERY_BOX")
                accept_mystery_box()
                time.sleep(3)
                detection_group = "POST_GAME"
                last_stage = None
            elif stage == "CONGRATULATIONS":
                print("🎉 Detected Stage: CONGRATULATIONS")
                accept_congratulations()
                detection_group = "POST_GAME"
                last_stage = None
            elif stage == "LEVEL_UP":
                print("⬆️ Detected Stage: LEVEL_UP")
                accept_level_up()
                detection_group = "PRE_GAME"
            elif stage == "DAILY_CHECKIN":
                print("📅 Detected Stage: DAILY_CHECKIN")
                accept_daily_checkin()
                detection_group = "PRE_GAME"
            elif stage == "DAILY_CHECKIN_BOOST_SET":
                print("📅 Detected Stage: DAILY_CHECKIN_BOOST_SET")
                accept_daily_checkin_boost_set()
                detection_group = "PRE_GAME"
            elif stage == "DAILY_TREASURE":
                print("💎 Detected Stage: DAILY_TREASURE")
                accept_daily_treasure()
                detection_group = "PRE_GAME"
            elif stage == "DAILY_NEW":
                print("📰 Detected Stage: DAILY_NEW")
                accept_daily_new()
                detection_group = "PRE_GAME"
            elif stage == "ENTER_LEAGUE":
                print("🏆 Detected Stage: ENTER_LEAGUE")
                accept_enter_league()
                detection_group = "PRE_GAME"
            elif stage == "LEAGUE_RESULTS":
                print("🏆 Detected Stage: LEAGUE_RESULTS")
                accept_league_results()
                detection_group = "PRE_GAME"
            elif stage == "PREVIOUS_RANK_RESULTS":
                print("🏆 Detected Stage: PREVIOUS_RANK_RESULTS")
                accept_previous_rank_results()
                detection_group = "PRE_GAME"
            elif stage == "OVERTAKE_BREAK_SCORE":
                print("🏆 Detected Stage: OVERTAKE_BREAK_SCORE")
                accept_overtake_break_score()
                detection_group = "POST_GAME"
                last_stage = None
            elif stage == "TOO_MANY_TREASURES":
                print("💎 Detected Stage: TOO_MANY_TREASURES")
                accept_too_many_treasures()
                detection_group = "PRE_GAME"
            elif stage == "RELIC_COMPLETE":
                print("🏺 Detected Stage: RELIC_COMPLETE")
                open_relic_complete()
                detection_group = "PRE_GAME"
            elif stage == "RELIC_CLAIM":
                print("🏺 Detected Stage: RELIC_CLAIM")
                accept_relic_claim()
                detection_group = "PRE_GAME"
            elif stage == "ANTI_BOT":
                print("⚠️ Detected Stage: ANTI_BOT")
                handle_anti_bot(device_screen)
                last_stage = None
            elif stage == "CONNECTION_LOST":
                print("🔌 Detected Stage: CONNECTION_LOST")
                runtime_config.set_stage("Restarting the game (connection lost)...")
                device_reset_app(device_ip, device_port)
                time.sleep(5)
                close_announcement_dialog()
                session_start_time = time.time()
                session_reset_interval = random.uniform(*SESSION_RESET_INTERVAL)
                last_lives_time = time.time()
                lives_interval = random.uniform(25 * 60, 35 * 60)
                detection_group = "PRE_GAME"
                last_stage = None
                is_first_game = True
            elif stage == "INACTIVE":
                print("💤 Detected Stage: INACTIVE")
                handle_inactive()
                last_stage = None
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("🛑 Bot stopped by user.")
        runtime_config.update(running=False, in_game=False)
        runtime_config.set_stage("Stopped")
    except Exception as e:
        print(f"❌ An error occurred: {e}")
        # The loop is gone, so do not leave the config UI claiming it runs.
        runtime_config.update(running=False, in_game=False)
        runtime_config.set_stage(f"Stopped after an error: {e}")
