"""
DBD Map Overlay
---------------
Shows a Dead by Daylight callout map image as an always-on-top overlay
while you play.

Setup:
    pip install pillow pywin32 pystray pytesseract
    Also install Tesseract-OCR itself (separate from the pip package):
    https://github.com/UB-Mannheim/tesseract/wiki

Run:
    python overlay.py

IMPORTANT: DBD must run in "Borderless Windowed" mode (Options > Graphics)
for the overlay to be visible on top of the game. Exclusive Fullscreen mode
will hide it, same as it would hide Discord's overlay or Alt+Tab.

Controls (defaults -- see "Customizing keybinds" below to change any of these):
    3           Toggle overlay visibility
    4  / 5      Previous / next map
    6  / 7      Jump back / forward 5 maps
    8  / 9      Reduce / increase map size
    0           Toggle auto map detection (reads the map name off your
                screen during the loading screen and switches automatically)
    Ctrl+H      Show/hide the on-screen keybinds list (shown automatically
                the first time you ever launch)
    Ctrl+G      Toggle whether hotkeys only work while DBD is focused, or
                always (see below)
    Ctrl+Drag   Move the overlay anywhere on screen (position is remembered)

H and G specifically require Ctrl because they're normal letters people
need to type in DBD's own text chat -- the digits are far less likely to
come up there, so they're left as plain keys for faster access.

By default, ALL hotkeys only stay active while DBD itself is the focused
window -- released the instant you tab away, so they never interfere with
typing in Discord, a browser, or anywhere else. Press Ctrl+G (or use the
tray menu) to switch to "always active" mode instead, where hotkeys work
everywhere regardless of which window is focused -- handy if you want to
control the overlay while tabbed out, at the cost of the plain digit keys
potentially catching keystrokes in other apps. This choice is remembered
across restarts (in overlay_settings.json).

If hotkeys never seem to activate at all while in DBD, its actual process
name may not match what this script expects -- run with --debug-focus to
print both the true foreground process and the topmost visible window's
process every half second, and compare them against DBD_PROCESS_NAMES near
the top of this file. (Both are checked because Windows can briefly delay
handing true focus to a freshly-launched window even once it's visually on
top -- e.g. right after Steam launches it -- so either one matching is
enough to count as "DBD is active".)

Customizing keybinds:
    The first time you run this, it writes overlay_settings.json (next to
    this script) with a "hotkeys" section listing every action, its key,
    and whether Ctrl is required, e.g.:
        "next_map": {"key": "5", "ctrl": false}
    Edit any "key" or "ctrl" value and restart the app to change it. Which
    action each entry controls is fixed -- only the key/ctrl assigned to
    it is yours to change.

Right-click the tray icon (bottom-right of your taskbar) for the same
actions, plus quit.

The overlay is click-through -- it will never intercept your mouse clicks,
so it won't interfere with gameplay.

Auto map detection (OCR):
    Once per second, a background thread grabs a small screenshot of the
    bottom-left corner of your screen (where DBD prints the map name on
    the loading screen), reads the text with Tesseract OCR, and switches
    the overlay automatically if it's confident about the match. It's
    reading pixels off your screen, not DBD's memory, so it carries no
    anti-cheat risk.

    The capture region is calibrated for a 2560x1440 screen and self-scales
    to other resolutions, but it's still worth double-checking. Run:
        python overlay.py --calibrate-ocr
    This saves ocr_debug_region1.png and ocr_debug_region2.png (next to
    this script) every second showing exactly what's being captured -- open
    it during a loading screen and check the map name is fully visible
    inside the crop. If not, adjust OCR_REGION_LEFT_FRAC /
    OCR_REGION_BOTTOM_MARGIN_FRAC / OCR_REGION_WIDTH_FRAC /
    OCR_REGION_HEIGHT_FRAC near the top of this file and try again.
"""

import os
import re
import sys
import json
import glob
import time
import ctypes
from ctypes import wintypes
import difflib
import threading

import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

try:
    import pystray
    from pystray import MenuItem as Item
except ImportError:
    pystray = None

try:
    import win32gui
    import win32con
    import win32api
    import win32event
    import winerror
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import pytesseract
    from PIL import ImageGrab
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

if HAS_OCR:
    def _find_tesseract_cmd():
        env_override = os.environ.get("TESSERACT_CMD")
        if env_override and os.path.isfile(env_override):
            return env_override

        import shutil
        on_path = shutil.which("tesseract")
        if on_path:
            return on_path

        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
        return None

    _tess_cmd = _find_tesseract_cmd()
    if _tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = _tess_cmd
    else:
        HAS_OCR = False

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR

if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    _log_path = os.path.join(BASE_DIR, "overlay_log.txt")
    _log_file = open(_log_path, "w", buffering=1, encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file

MAPS_DIR = os.path.join(BASE_DIR, "maps")
SETTINGS_PATH = os.path.join(BASE_DIR, "overlay_settings.json")
SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".webp")

DEFAULT_WIDTH = 380
MIN_WIDTH = 150
MAX_WIDTH = 900
STEP = 40
JUMP = 5
MARGIN = 10

TRANSPARENT_KEY = "#FF00FF"

OCR_INTERVAL_SECONDS = 0.75
OCR_REGION_LEFT_FRAC = 0.035
OCR_REGION_WIDTH_FRAC = 0.5616  # was 0.39 originally; widened 20% then another 20% so longer map names aren't cut off
OCR_REGION_BOTTOM_MARGIN_FRAC = 0.10
OCR_REGION_HEIGHT_FRAC = 0.085
OCR_MATCH_THRESHOLD = 0.8

OCR_REGION2_WIDTH_FRAC = 0.3125
OCR_REGION2_BOTTOM_MARGIN_FRAC = 0.125
OCR_REGION2_HEIGHT_FRAC = 0.09
OCR_MATCH_THRESHOLD_2 = 0.6

DBD_PROCESS_NAMES = {"deadbydaylight-win64-shipping.exe"}
FOCUS_POLL_INTERVAL = 0.5

# Default keybinds -- overridable per-action in overlay_settings.json under
# a "hotkeys" key, e.g.:
#   "hotkeys": { "next_map": {"key": "5", "ctrl": false}, ... }
# Only "key" (single character) and "ctrl" (true/false) are user-editable;
# which action each one triggers is fixed. Restart the app after editing.
DEFAULT_HOTKEYS = {
    "toggle_visibility": {"key": "3", "ctrl": False, "label": "toggle visibility"},
    "prev_map":          {"key": "4", "ctrl": False, "label": "previous map"},
    "next_map":          {"key": "5", "ctrl": False, "label": "next map"},
    "jump_backward":     {"key": "6", "ctrl": False, "label": "jump back 5"},
    "jump_forward":      {"key": "7", "ctrl": False, "label": "jump forward 5"},
    "shrink":            {"key": "8", "ctrl": False, "label": "reduce map size"},
    "grow":              {"key": "9", "ctrl": False, "label": "increase map size"},
    "toggle_ocr":        {"key": "0", "ctrl": False, "label": "toggle auto map detection"},
    # Ctrl required here specifically -- H is a normal letter people need to
    # type in DBD's own chat, unlike the digits above which chat rarely needs.
    "toggle_keybinds":     {"key": "h", "ctrl": True, "label": "show/hide this keybinds list"},
    "toggle_focus_gating": {"key": "g", "ctrl": True, "label": "toggle: hotkeys only in DBD vs always active"},
}


def load_map_files():
    files = []
    for ext in SUPPORTED_EXT:
        files.extend(glob.glob(os.path.join(MAPS_DIR, f"*{ext}")))
    files.sort()
    return files


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"! Couldn't read {SETTINGS_PATH} ({e}) -- using defaults instead.")
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"! Failed to save settings: {e}")


def _to_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


class OverlayApp:
    def __init__(self, root, calibrate_ocr=False, debug_focus=False):
        self.root = root
        self.maps = load_map_files()
        if not self.maps:
            print(f"No map images found in {MAPS_DIR}")
            print("Run scraper.py first, or drop your own map images into that folder.")
            sys.exit(1)

        self.settings = load_settings()
        is_first_run = not self.settings.get("has_launched_before", False)
        self.pos_x = self.settings.get("pos_x", MARGIN)
        self.pos_y = self.settings.get("pos_y", MARGIN)
        # True (default): hotkeys only work while DBD is the focused window.
        # False: hotkeys work everywhere, all the time -- useful if you
        # want to control the overlay while tabbed out, at the cost of
        # keys like the digits potentially catching typing in other apps.
        self.focus_gated = _to_bool(self.settings.get("hotkeys_only_when_focused", True), True)

        self.index = 0
        self.width = DEFAULT_WIDTH
        self.visible = True
        self.debug_focus = debug_focus
        self.ocr_enabled = True
        self.ocr_debug = calibrate_ocr
        self.ocr_lookup = self._build_ocr_lookup()
        self.hotkey_list = []
        self.keybinds_visible = is_first_run
        self._hwnd = None
        self._interactive = False
        self._drag_data = None

        self.settings["has_launched_before"] = True
        save_settings(self.settings)

        self.root.title("DBD Map Overlay")  # so prevent_multiple_instances() can find this window
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        self.label = tk.Label(self.root, bg=TRANSPARENT_KEY, bd=0)
        self.label.pack()

        self.name_label = tk.Label(
            self.root, bg=TRANSPARENT_KEY, fg="white", font=("Segoe UI", 9, "bold")
        )
        self.name_label.pack()

        self.position_label = tk.Label(
            self.root, bg=TRANSPARENT_KEY, fg="#dddddd", font=("Segoe UI", 8)
        )
        self.position_label.pack()

        self.ocr_status_label = tk.Label(
            self.root, bg=TRANSPARENT_KEY, font=("Segoe UI", 8, "bold")
        )
        self.ocr_status_label.pack()
        self._update_ocr_status_label()

        self.keybinds_label = tk.Label(
            self.root, bg=TRANSPARENT_KEY, fg="#eeeeee", font=("Consolas", 8),
            justify="left", anchor="w",
        )

        self.tk_img = None
        self.render_current_map()
        self._setup_drag()

        self.root.geometry(f"+{self.pos_x}+{self.pos_y}")

        if HAS_WIN32:
            self.root.after(200, self.make_click_through)
        else:
            msg = (
                "pywin32 not installed -- overlay won't be click-through.\n\n"
                "Clicking on it will interact with the overlay window itself "
                "instead of the game, and if DBD is set to Exclusive "
                "Fullscreen, that can minimize the game entirely."
            )
            print(f"Note: {msg}")
            try:
                messagebox.showwarning("DBD Map Overlay", msg)
            except Exception:
                pass

        self.setup_hotkeys()
        self.hotkey_list.append(("Ctrl+Drag", "move the overlay"))
        self._refresh_keybinds_panel()
        if pystray:
            threading.Thread(target=self.setup_tray, daemon=True).start()
        else:
            print("Note: pystray not installed -- no tray menu, hotkeys only.")
            print("Install it with: pip install pystray")

        if HAS_OCR:
            threading.Thread(target=self._ocr_loop, daemon=True).start()
            if calibrate_ocr:
                print("Calibration mode: saving ocr_debug_region1.png and")
                print("ocr_debug_region2.png every second. Open them during")
                print("the relevant screen and check the text is fully visible.")
        else:
            print("Note: auto map detection unavailable -- Tesseract-OCR not found.")
            print("Install it from https://github.com/UB-Mannheim/tesseract/wiki")
            print(r"Default install path checked: C:\Program Files\Tesseract-OCR\tesseract.exe")

    def render_current_map(self):
        path = self.maps[self.index]
        img = Image.open(path).convert("RGBA")
        ratio = self.width / img.width
        new_size = (self.width, max(1, int(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(img)
        self.label.configure(image=self.tk_img)
        name = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        self.name_label.configure(text=name)
        self.position_label.configure(text=f"{self.index + 1}/{len(self.maps)}")
        self._resize_to_content()

    def _resize_to_content(self):
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        self.root.geometry(f"{w}x{h}+{self.pos_x}+{self.pos_y}")

    def set_map_by_path(self, path):
        if path in self.maps:
            self.index = self.maps.index(path)
            self.render_current_map()

    def next_map(self):
        self.index = (self.index + 1) % len(self.maps)
        self.render_current_map()

    def prev_map(self):
        self.index = (self.index - 1) % len(self.maps)
        self.render_current_map()

    def jump_forward(self):
        self.index = (self.index + JUMP) % len(self.maps)
        self.render_current_map()

    def jump_backward(self):
        self.index = (self.index - JUMP) % len(self.maps)
        self.render_current_map()

    def grow(self):
        self.width = min(MAX_WIDTH, self.width + STEP)
        self.render_current_map()

    def shrink(self):
        self.width = max(MIN_WIDTH, self.width - STEP)
        self.render_current_map()

    def toggle_visibility(self):
        self.visible = not self.visible
        if self.visible:
            self.root.deiconify()
        else:
            self.root.withdraw()

    def toggle_ocr(self):
        self.ocr_enabled = not self.ocr_enabled
        print(f"Auto map detection: {'ON' if self.ocr_enabled else 'OFF'}")
        self._update_ocr_status_label()

    def _update_ocr_status_label(self):
        if self.ocr_enabled:
            self.ocr_status_label.configure(text="AUTO: ON", fg="#7CFC7C")
        else:
            self.ocr_status_label.configure(text="AUTO: OFF", fg="#ff6b6b")

    def toggle_keybinds(self):
        self.keybinds_visible = not self.keybinds_visible
        self._refresh_keybinds_panel()

    def toggle_focus_gating(self):
        self.focus_gated = not self.focus_gated
        self.settings["hotkeys_only_when_focused"] = self.focus_gated
        save_settings(self.settings)
        state = "only while DBD is focused" if self.focus_gated else "always active, everywhere"
        print(f"Hotkeys: {state}")

    def _refresh_keybinds_panel(self):
        if self.keybinds_visible:
            lines = [f"{key} - {label}" for key, label in self.hotkey_list]
            self.keybinds_label.configure(text="\n".join(lines))
            self.keybinds_label.pack()
        else:
            self.keybinds_label.pack_forget()
        self._resize_to_content()

    def make_click_through(self):
        try:
            hwnd = win32gui.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            self._hwnd = hwnd

            styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd,
                win32con.GWL_EXSTYLE,
                styles | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT,
            )
            r = int(TRANSPARENT_KEY[1:3], 16)
            g = int(TRANSPARENT_KEY[3:5], 16)
            b = int(TRANSPARENT_KEY[5:7], 16)
            colorkey = win32api.RGB(r, g, b)
            win32gui.SetLayeredWindowAttributes(hwnd, colorkey, 0, win32con.LWA_COLORKEY)

            confirmed = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if confirmed & win32con.WS_EX_TRANSPARENT:
                print("Click-through + transparency enabled.")
                threading.Thread(target=self._ctrl_watch_loop, daemon=True).start()
            else:
                print("! Click-through style didn't stick (hwnd may be wrong).")
        except Exception as e:
            msg = f"Click-through setup failed: {e}"
            print(f"! {msg}")
            try:
                messagebox.showwarning("DBD Map Overlay", msg)
            except Exception:
                pass

    def _ctrl_watch_loop(self):
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        was_down = False
        while True:
            time.sleep(0.05)
            is_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            if is_down != was_down:
                was_down = is_down
                self.root.after(0, self._set_interactive, is_down)

    def _set_interactive(self, enabled):
        self._interactive = enabled
        if not self._hwnd:
            return
        try:
            styles = win32gui.GetWindowLong(self._hwnd, win32con.GWL_EXSTYLE)
            if enabled:
                styles &= ~win32con.WS_EX_TRANSPARENT
            else:
                styles |= win32con.WS_EX_TRANSPARENT
            win32gui.SetWindowLong(self._hwnd, win32con.GWL_EXSTYLE, styles)
        except Exception as e:
            print(f"! Failed to toggle drag mode: {e}")

    def _on_drag_start(self, event):
        if not self._interactive:
            return
        self._drag_data = (event.x_root, event.y_root, self.pos_x, self.pos_y)

    def _on_drag_motion(self, event):
        if not self._interactive or not self._drag_data:
            return
        start_x, start_y, win_x, win_y = self._drag_data
        self.pos_x = win_x + (event.x_root - start_x)
        self.pos_y = win_y + (event.y_root - start_y)
        self.root.geometry(f"+{self.pos_x}+{self.pos_y}")

    def _on_drag_end(self, event):
        if self._drag_data:
            self._drag_data = None
            self.settings["pos_x"] = self.pos_x
            self.settings["pos_y"] = self.pos_y
            save_settings(self.settings)

    def _setup_drag(self):
        for widget in (
            self.label, self.name_label, self.position_label,
            self.ocr_status_label, self.keybinds_label,
        ):
            widget.bind("<ButtonPress-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_end)

    @staticmethod
    def _normalize(text):
        text = text.lower()
        text = re.sub(r"[^a-z0-9 ]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_ocr_lookup(self):
        lookup = {}
        for p in self.maps:
            name = os.path.splitext(os.path.basename(p))[0].replace("_", " ")
            lookup[self._normalize(name)] = p
        self._candidate_words = {
            name: name.split() for name in lookup
        }
        return lookup

    @staticmethod
    def _words_match(word_a, word_b):
        if word_a == word_b:
            return True
        return difflib.SequenceMatcher(None, word_a, word_b).ratio() > 0.82

    def _best_match(self, normalized_text):
        ocr_words = normalized_text.split()
        if not ocr_words:
            return None, 0.0

        best_name, best_score = None, 0.0
        for name, cand_words in self._candidate_words.items():
            if not cand_words:
                continue
            hits = sum(
                1 for cw in cand_words
                if any(self._words_match(cw, ow) for ow in ocr_words)
            )
            score = hits / len(cand_words)
            if score > best_score:
                best_score, best_name = score, name
        return best_name, best_score

    def _ocr_loop(self):
        script_dir = BASE_DIR

        while True:
            time.sleep(OCR_INTERVAL_SECONDS)
            if not self.ocr_enabled:
                continue

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()

            r1_left = int(screen_w * OCR_REGION_LEFT_FRAC)
            r1_right = r1_left + int(screen_w * OCR_REGION_WIDTH_FRAC)
            r1_bottom = int(screen_h * (1 - OCR_REGION_BOTTOM_MARGIN_FRAC))
            r1_top = r1_bottom - int(screen_h * OCR_REGION_HEIGHT_FRAC)

            r2_width = int(screen_w * OCR_REGION2_WIDTH_FRAC)
            r2_left = int(screen_w * 0.5 - r2_width / 2)
            r2_right = r2_left + r2_width
            r2_bottom = int(screen_h * (1 - OCR_REGION2_BOTTOM_MARGIN_FRAC))
            r2_top = r2_bottom - int(screen_h * OCR_REGION2_HEIGHT_FRAC)

            regions = [
                ("region1", r1_left, r1_top, r1_right, r1_bottom, OCR_MATCH_THRESHOLD),
                ("region2", r2_left, r2_top, r2_right, r2_bottom, OCR_MATCH_THRESHOLD_2),
            ]

            matched_path = None
            matched_name = None

            for name, left, top, right, bottom, threshold in regions:
                try:
                    crop = ImageGrab.grab(bbox=(left, top, right, bottom))
                    if self.ocr_debug:
                        crop.save(os.path.join(script_dir, f"ocr_debug_{name}.png"))
                    text = pytesseract.image_to_string(crop)
                except Exception as e:
                    print(f"! OCR capture failed ({name}): {e}")
                    continue

                normalized = self._normalize(text)
                if not normalized:
                    continue

                best_name, best_score = self._best_match(normalized)
                if best_name and best_score >= threshold:
                    matched_name = best_name
                    matched_path = self.ocr_lookup[best_name]
                    break

            if matched_path and matched_path != self.maps[self.index]:
                print(f"Auto-detected map: {matched_name}")
                self.root.after(0, self.set_map_by_path, matched_path)

    def setup_hotkeys(self):
        if not HAS_WIN32:
            print("Note: pywin32 not installed -- no global hotkeys.")
            return

        func_map = {
            "toggle_visibility": self.toggle_visibility,
            "prev_map": self.prev_map,
            "next_map": self.next_map,
            "jump_backward": self.jump_backward,
            "jump_forward": self.jump_forward,
            "shrink": self.shrink,
            "grow": self.grow,
            "toggle_ocr": self.toggle_ocr,
            "toggle_keybinds": self.toggle_keybinds,
            "toggle_focus_gating": self.toggle_focus_gating,
        }

        configured = self.settings.get("hotkeys", {})
        if not isinstance(configured, dict):
            configured = {}

        resolved = {}
        bindings = []
        for action_id, default in DEFAULT_HOTKEYS.items():
            override = configured.get(action_id, {})
            if not isinstance(override, dict):
                override = {}

            key = str(override.get("key", default["key"])).strip()[:1] or default["key"]
            ctrl = _to_bool(override.get("ctrl", default["ctrl"]), default["ctrl"])
            label = default["label"]

            resolved[action_id] = {"key": key, "ctrl": ctrl}
            bindings.append((key, ctrl, func_map[action_id], label))

        # Write the resolved set back so the settings file always shows the
        # full, currently-active keybinds -- readable and editable even if
        # the user has never touched this section before.
        self.settings["hotkeys"] = resolved
        save_settings(self.settings)
        print(f"Hotkeys loaded from {SETTINGS_PATH} (edit that file and restart to change them).")

        self.hotkey_list = [
            (f"{'Ctrl+' if ctrl else ''}{key.upper()}", label)
            for key, ctrl, _func, label in bindings
        ]
        self.hotkey_display = {
            action_id: f"{'Ctrl+' if v['ctrl'] else ''}{v['key'].upper()}"
            for action_id, v in resolved.items()
        }

        threading.Thread(
            target=self._hotkey_message_loop, args=(bindings,), daemon=True
        ).start()

    def _hotkey_message_loop(self, bindings):
        user32 = ctypes.windll.user32
        WM_HOTKEY = 0x0312
        PM_REMOVE = 0x0001
        MOD_CONTROL = 0x0002

        id_to_func = {i: func for i, (_key, _ctrl, func, _label) in enumerate(bindings, start=1)}
        registered = False

        def register_all():
            nonlocal registered
            ok = True
            for i, (key, ctrl, _func, label) in enumerate(bindings, start=1):
                vk = ord(key.upper())
                mods = MOD_CONTROL if ctrl else 0
                if not user32.RegisterHotKey(None, i, mods, vk):
                    ok = False
                    combo = f"Ctrl+{key.upper()}" if ctrl else key.upper()
                    print(f"! Hotkey [{combo}] -> {label} failed to register "
                          "(maybe a duplicate, or already used by another app)")
            registered = True
            if ok:
                print("Hotkeys active (DBD is focused).")

        def unregister_all():
            nonlocal registered
            for i in id_to_func:
                user32.UnregisterHotKey(None, i)
            registered = False
            print("Hotkeys released (DBD not focused).")

        msg = wintypes.MSG()
        was_should_be_registered = None
        last_check = 0.0
        try:
            while True:
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        func = id_to_func.get(msg.wParam)
                        if func:
                            self.root.after(0, func)

                now = time.time()
                if now - last_check >= FOCUS_POLL_INTERVAL:
                    last_check = now
                    if self.focus_gated:
                        should_be_registered = self._is_dbd_foreground()
                    else:
                        should_be_registered = True  # always-active mode

                    if self.debug_focus:
                        fg_name = self._get_foreground_process_name()
                        top_name = self._get_topmost_real_window_process()
                        print(f"[debug-focus] foreground={fg_name} topmost={top_name} "
                              f"(focus_gated={self.focus_gated})")

                    if should_be_registered != was_should_be_registered:
                        was_should_be_registered = should_be_registered
                        if should_be_registered and not registered:
                            register_all()
                        elif not should_be_registered and registered:
                            unregister_all()

                time.sleep(0.02)
        finally:
            if registered:
                for i in id_to_func:
                    user32.UnregisterHotKey(None, i)

    @staticmethod
    def _process_name_from_pid(pid):
        if not pid:
            return None
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hproc:
            return None
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(hproc)
        return None

    @classmethod
    def _get_foreground_process_name(cls):
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return cls._process_name_from_pid(pid.value)

    def _get_topmost_real_window_process(self):
        """Windows can withhold true input focus (GetForegroundWindow) from
        a freshly-launched window -- e.g. one spawned by Steam -- until the
        user clicks into it, even though it's already the topmost thing on
        screen. This walks the actual on-screen window stack, top to
        bottom, and returns the process owning the first real, visible,
        non-minimized window (skipping this app's own always-on-top
        overlay), so a game that's visually active gets detected even
        before Windows officially hands it focus."""
        user32 = ctypes.windll.user32
        result = {"name": None}

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if hwnd == self._hwnd:
                return True  # skip our own overlay window, keep looking
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True  # hidden or minimized, keep looking
            if user32.GetWindowTextLengthW(hwnd) == 0:
                return True  # not a real top-level app window, keep looking
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result["name"] = self._process_name_from_pid(pid.value)
            return False  # found the topmost real window -- stop

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        return result["name"]

    def _is_dbd_foreground(self):
        name = self._get_foreground_process_name()
        if name and name in DBD_PROCESS_NAMES:
            return True
        topmost_name = self._get_topmost_real_window_process()
        return bool(topmost_name) and topmost_name in DBD_PROCESS_NAMES

    def setup_tray(self):
        def safe(func, *args):
            def handler(icon, item):
                try:
                    self.root.after(0, func, *args)
                except Exception as e:
                    print(f"! Tray action failed: {e}")
            return handler

        menu_items = [
            Item(f"Toggle overlay ({self.hotkey_display['toggle_visibility']})", safe(self.toggle_visibility)),
            Item(f"Previous map ({self.hotkey_display['prev_map']})", safe(self.prev_map)),
            Item(f"Next map ({self.hotkey_display['next_map']})", safe(self.next_map)),
            Item(f"Jump back 5 ({self.hotkey_display['jump_backward']})", safe(self.jump_backward)),
            Item(f"Jump forward 5 ({self.hotkey_display['jump_forward']})", safe(self.jump_forward)),
            Item(f"Reduce map size ({self.hotkey_display['shrink']})", safe(self.shrink)),
            Item(f"Increase map size ({self.hotkey_display['grow']})", safe(self.grow)),
            Item(f"Toggle auto map detection ({self.hotkey_display['toggle_ocr']})", safe(self.toggle_ocr)),
            Item(f"Show/hide keybinds ({self.hotkey_display['toggle_keybinds']})", safe(self.toggle_keybinds)),
            Item(
                f"Hotkeys active everywhere, not just in DBD ({self.hotkey_display['toggle_focus_gating']})",
                safe(self.toggle_focus_gating),
                checked=lambda item: not self.focus_gated,
            ),
            Item("Quit", safe(self.quit_app)),
        ]

        image = self._load_tray_icon()
        self.tray_icon = pystray.Icon(
            "dbd_overlay", image, "DBD Map Overlay", pystray.Menu(*menu_items)
        )
        self.tray_icon.run()

    @staticmethod
    def _load_tray_icon():
        logo_path = os.path.join(RESOURCE_DIR, "assets", "logo.png")
        try:
            return Image.open(logo_path)
        except Exception:
            return Image.new("RGB", (64, 64), "black")

    def quit_app(self):
        if pystray and hasattr(self, "tray_icon"):
            self.tray_icon.stop()
        self.root.destroy()
        sys.exit(0)


_app_mutex = None

def prevent_multiple_instances():
    global _app_mutex
    if not HAS_WIN32:
        return False
        
    mutex_name = "DBD_Overlay_Unique_Mutex_Name"
    _app_mutex = win32event.CreateMutex(None, False, mutex_name)
    
    
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        hwnd = win32gui.FindWindow(None, "DBD Map Overlay")
        if hwnd:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        return True
    return False


def main():
    if prevent_multiple_instances():
        sys.exit(0)

    calibrate = "--calibrate-ocr" in sys.argv
    debug_focus = "--debug-focus" in sys.argv
    root = tk.Tk()
    OverlayApp(root, calibrate_ocr=calibrate, debug_focus=debug_focus)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        err = traceback.format_exc()
        print(err)
        try:
            messagebox.showerror("DBD Map Overlay - Crash", err)
        except Exception:
            pass
        raise
