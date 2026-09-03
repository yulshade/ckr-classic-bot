import os

import cv2
import numpy as np

from config import (
    ANTI_BOT_CARD_HEIGHT,
    ANTI_BOT_CARD_POS_6,
    ANTI_BOT_CARD_WIDTH,
    ANTI_BOT_CARD_POS_1,
    ANTI_BOT_CARD_POS_2,
    ANTI_BOT_CARD_POS_3,
    ANTI_BOT_CARD_POS_4,
    ANTI_BOT_CARD_POS_5,
    ANTI_BOT_CARD_POS_6,
    BOOST_TEMPLATES,
    IN_RUN_REGION,
    IN_RUN_TEMPLATE,
    MATCH_THRESHOLD,
    STAGE_REGIONS,
    STAGE_TEMPLATES,
    TEMPLATE_DIR,
)


_template_cache: dict = {}
_template_gray_cache: dict = {}


def _get_template(filename):
    """Return cached template image, loading from disk on first access."""
    if filename not in _template_cache:
        path = os.path.join(TEMPLATE_DIR, filename)
        _template_cache[filename] = _normalize(cv2.imread(path, cv2.IMREAD_UNCHANGED))
    return _template_cache[filename]


def _get_template_gray(filename):
    """Return cached grayscale template image, loading from disk on first access."""
    if filename not in _template_gray_cache:
        template = _get_template(filename)
        _template_gray_cache[filename] = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY) if template is not None else None
    return _template_gray_cache[filename]


def unmatchable_stages():
    """
    Stages whose template can never fit inside their region, so they never match.

    detect_stage skips a template larger than its search area in silence, which
    makes the stage look like a detection miss rather than a config error -- and
    a region only a few pixels too small fails exactly as completely as one that
    is wildly wrong. Reported at startup so the mismatch is visible.
    """
    problems = []
    for stage_name, template_files in STAGE_TEMPLATES.items():
        region = STAGE_REGIONS.get(stage_name)
        if region is None:
            continue
        region_w, region_h = region[2] - region[0], region[3] - region[1]
        for filename in template_files:
            template = _get_template_gray(filename)
            if template is None:
                continue
            th, tw = template.shape[:2]
            if tw > region_w or th > region_h:
                problems.append((stage_name, filename, (tw, th), (region_w, region_h)))
    return problems


def load_templates():
    """Pre-warm the template cache with all stage and boost templates at startup."""
    for template_files in STAGE_TEMPLATES.values():
        for filename in template_files:
            _get_template_gray(filename)
    for template_files in BOOST_TEMPLATES:
        for filename in template_files:
            _get_template_gray(filename)
    for filename in IN_RUN_TEMPLATE:
        _get_template_gray(filename)


def _normalize(img):
    """Ensure image is BGR uint8 (3-channel). Returns None if conversion fails."""
    if img is None:
        return None
    if img.dtype != np.uint8:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.ndim == 3 and img.shape[2] == 3:
        return img
    return None


def _normalize_gray(img):
    normalized = _normalize(img)
    if normalized is None:
        return None
    return cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY)


def _crop_region(img, region):
    if region is None:
        return img
    x1, y1, x2, y2 = region
    return img[y1:y2, x1:x2]


def detect_templates(screen, template_files, region=None):
    screen_gray = _normalize_gray(screen)
    if screen_gray is None:
        return []
    screen_gray = _crop_region(screen_gray, region)
    offset_x, offset_y = (region[0], region[1]) if region is not None else (0, 0)
    matches = []
    for filename in template_files:
        template = _get_template_gray(filename)
        if template is None:
            continue
        result = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= MATCH_THRESHOLD:
            th, tw = template.shape[:2]
            x = max_loc[0] + offset_x
            y = max_loc[1] + offset_y
            matches.append((x, y, tw, th))
    return matches


def detect_stage(screen, stage_names=None, exclude=None):
    screen_gray = _normalize_gray(screen)
    if screen_gray is None:
        return None
    if stage_names is None:
        stage_names = STAGE_TEMPLATES.keys()
    if exclude:
        stage_names = [s for s in stage_names if s not in exclude]
    for stage_name in stage_names:
        template_files = STAGE_TEMPLATES.get(stage_name)
        if not template_files:
            continue
        search_area = _crop_region(screen_gray, STAGE_REGIONS.get(stage_name))
        for filename in template_files:
            template = _get_template_gray(filename)
            if template is None:
                continue
            if (
                search_area.shape[0] < template.shape[0]
                or search_area.shape[1] < template.shape[1]
            ):
                continue
            result = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val >= MATCH_THRESHOLD:
                return stage_name
    return None


def run_probe_ready():
    """Whether the run-alive probe has a usable template to answer with."""
    return any(_get_template_gray(filename) is not None for filename in IN_RUN_TEMPLATE)


def detect_run_alive(screen):
    """
    Whether a run is on screen right now: True, False, or None for "can't tell".

    None means the probe has nothing to match with -- no IN_RUN_TEMPLATE file on
    disk, or one too large for its region -- so callers must treat it as unknown
    rather than as "no run": a run that is actually in progress also matches no
    stage, and mistaking one for the other restarts the game mid-run.
    """
    screen_gray = _normalize_gray(screen)
    if screen_gray is None:
        return None
    search_area = _crop_region(screen_gray, IN_RUN_REGION)
    matched_any_template = False
    for filename in IN_RUN_TEMPLATE:
        template = _get_template_gray(filename)
        if template is None:
            continue
        if (
            search_area.shape[0] < template.shape[0]
            or search_area.shape[1] < template.shape[1]
        ):
            continue
        matched_any_template = True
        result = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val >= MATCH_THRESHOLD:
            return True
    return False if matched_any_template else None


def detect_anti_bot_odd_cards(screen):
    """
    Return 0-based indices of the 2 cards that differ from the majority 4.

    Strategy:
      1. Crop each card region.
      2. Build a pairwise HSV-histogram similarity matrix (6x6).
      3. For each card, compute its average similarity to all others.
      4. The 2 cards with the lowest average similarity are the odd ones.
    """

    # Define card coordinates based on config constants
    card_coords = [
        ANTI_BOT_CARD_POS_1,
        ANTI_BOT_CARD_POS_2,
        ANTI_BOT_CARD_POS_3,
        ANTI_BOT_CARD_POS_4,
        ANTI_BOT_CARD_POS_5,
        ANTI_BOT_CARD_POS_6,
    ]

    # Crop card regions as grayscale for structural comparison
    screen_bgr = _normalize(screen)
    crops = []
    for cx, cy in card_coords:
        crop = screen_bgr[cy:cy + ANTI_BOT_CARD_HEIGHT, cx:cx + ANTI_BOT_CARD_WIDTH]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        crops.append(gray)

    # Pairwise structural similarity via normalized cross-correlation
    n = len(crops)
    sim = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                result = cv2.matchTemplate(crops[i], crops[j], cv2.TM_CCOEFF_NORMED)
                sim[i][j] = result[0][0]

    # Average similarity of each card against all others (excluding self)
    avg_sim = sim.sum(axis=1) / (n - 1)
    print("🔍 Analyzing card similarity...")
    for idx, s in enumerate(avg_sim):
        print(f"  Card {idx + 1}: similarity score {s:.2f}")

    # The 2 cards with the lowest average similarity are the odd ones
    odd_indices = list(np.argsort(avg_sim)[:2])
    return odd_indices
