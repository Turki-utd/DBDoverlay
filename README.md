# DBD Map Overlay

<img src="assets/logo.png" width="80" align="right" alt="DBD Map Overlay logo">

An always-on-top callout map overlay for Dead by Daylight. Pins the current
map's callout diagram to your screen while you play, with fully customizable
hotkeys, drag-to-reposition, a system tray menu, and optional automatic map
detection that reads the loading screen for you.

> **Unofficial fan project.** Not affiliated with or endorsed by Behaviour
> Interactive. See [Credits & disclaimer](#credits--disclaimer) below.

## Features

- **Click-through overlay** — never blocks your mouse or interferes with gameplay
- **Automatic map detection** — reads the map name off your loading screen with
  OCR and switches automatically. Only reads pixels on screen, never game
  memory, so it carries no anti-cheat risk
- **Fully customizable hotkeys** — every keybind (and whether it needs Ctrl)
  is editable in a plain settings file, no code changes needed
- **Smart focus handling** — hotkeys only activate while DBD is focused by
  default, so keys never interfere with typing in Discord, a browser, or
  DBD's own chat. Toggle to "always active" any time if you'd rather
- **Drag-to-move** — hold Ctrl and drag the overlay anywhere; it remembers
  where you put it
- **System tray menu** for every action, plus a keybinds list shown
  automatically the first time you launch

## Installation

1. Download the installer from [Releases](../../releases)
2. Run it — during setup you'll be asked whether to also install
   Tesseract-OCR, which is needed for automatic map detection (recommended,
   optional)
3. Launch **DBD Map Overlay** from the Start Menu or desktop shortcut

That's it — callout map images are bundled with the installer, so there's
nothing extra to download or set up.

## Running it

**Set DBD to Borderless Windowed** (Options > Graphics > Display Mode).
Exclusive Fullscreen hides *any* overlay, the same way it hides Discord's
or Steam's.

- Shows the map name and its position in your list, e.g. `17/57`
- A small **AUTO: ON / AUTO: OFF** indicator shows whether auto-detection
  is currently active
- The keybinds list shows itself automatically the first time you ever run
  the app, then stays hidden by default after that

### Default controls

| Key | Action |
|---|---|
| `3` | Toggle overlay visibility |
| `4` / `5` | Previous / next map |
| `6` / `7` | Jump back / forward 5 maps |
| `8` / `9` | Reduce / increase map size |
| `0` | Toggle auto map detection |
| `Ctrl+H` | Show/hide the keybinds list |
| `Ctrl+G` | Toggle "only active in DBD" vs "always active everywhere" |
| `Ctrl` + drag | Move the overlay anywhere on screen |

`H` and `G` specifically require Ctrl since they're letters you'd otherwise
need to type normally (e.g. in DBD's own chat) — the digits are far less
likely to come up there, so they're left as plain keys for faster access.

By default **all hotkeys only work while DBD is the focused window**,
releasing instantly the moment you tab away, so they never interfere with
typing anywhere else. Press `Ctrl+G` (or use the tray menu) to switch to
"always active" mode instead — useful if you want to control the overlay
while tabbed out. This choice is remembered across restarts.

Right-click the **tray icon** (taskbar, bottom-right) for all the same
actions, plus Quit.

### Customizing keybinds

The first launch writes `overlay_settings.json` next to the app with a
`hotkeys` section listing every action, its key, and whether Ctrl is
required:
```json
"next_map": {"key": "5", "ctrl": false}
```
Edit any `key` or `ctrl` value and restart to apply it. Which action each
entry controls is fixed — only what triggers it is yours to remap.

## Automatic map detection (OCR)

Once per second, a background thread grabs a small screenshot of the
bottom-left corner of your screen — where DBD shows the map name on the
loading screen — reads it with Tesseract OCR, and switches the overlay
automatically when it's confident about the match.

If you skipped installing Tesseract-OCR during setup, everything else still
works fine — you'll just switch maps manually with the keys above. Toggle
detection on/off any time with `0` or the tray menu.

**Calibrating:** the capture region is defined as a fraction of your screen
size, so it should land correctly regardless of resolution, but it's worth
double-checking if detection seems off. Run from a command prompt in the
install folder:
```
DBD_Overlay.exe --calibrate-ocr
```
This saves `ocr_debug_region1.png` / `ocr_debug_region2.png` next to the
exe every second, and prints exactly what OCR read and how it scored
against your maps. Load into a match, check the debug images during the
loading screen to confirm the map name is fully inside the crop, and watch
the terminal output to see what text was actually recognized.

## Notes

- This is a plain always-on-top window, the same category as a Discord or
  Spotify overlay — it doesn't read game memory or inject into the DBD
  process, so it's not the kind of thing that trips EasyAntiCheat.
- Hotkeys use Windows' native `RegisterHotKey` API rather than a global
  keyboard hook, which would process every keystroke system-wide (WASD,
  spacebar, everything) through Python before letting it through to the
  game — a real, measured FPS cost during gameplay. `RegisterHotKey` has
  Windows itself filter for the exact keys registered, so unrelated
  keystrokes never touch this app at all.

## Credits & disclaimer

- Callout map diagrams bundled with this app are sourced from
  [hens333.com](https://hens333.com) — all credit for that artwork belongs
  to its creator.
- The app logo is original artwork made for this project.
- Dead by Daylight is a trademark of Behaviour Interactive Inc. This is an
  unofficial, fan-made tool with no affiliation to Behaviour Interactive.

## License

MIT — see [LICENSE](LICENSE). Covers the code in this project only; see
the disclaimer above for map imagery and trademarks.
