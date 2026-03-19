# MGS Qt UI — Feature List

## Implemented

### Project File (.mtp)
- **Open Folder** — scan a folder case-insensitively for RADIO.DAT, DEMO.DAT, VOX.DAT, ZMOVIE.STR and load all found at once
- **Open Project (.mtp)** — restore all subtitle edits and attempt to reload audio from stored DAT paths and ZMOVIE.STR; prompts to relocate missing files
- **Save Project / Save Project As** (Ctrl+S / Cmd+Shift+S) — ZIP archive containing settings.json, radio.xml, {demo,vox,zmovie}-original.json, {demo,vox,zmovie}-altered.json, {vox,demo}-offsets.json, font.tbl
- **Backward compatibility** — old `.mtp` files with single `*-dialogue.json` are migrated on open (treated as original with no alterations)
- **JSON v1/v2 auto-conversion** — legacy v1 format JSONs (list-based `[texts, timings]`) are auto-converted to v2 format (keyed by start frame) on project load for demo, vox, and zmovie
- **Quick-access buttons** — Open Folder, Open Project, and Finalize Project buttons in the lower-right panel for one-click access without opening the menu

### Editor Modes
- **Radio Mode** — browse codec calls by offset, select VOX_CUES, edit subtitle text, play audio via VOX.DAT
- **Demo Mode** — browse DEMO.DAT entries with dialogue, edit subtitle text and timing, play audio with subtitle overlay
- **VOX Mode** — same as Demo mode but for VOX.DAT clips (independent subtitle/timing editing)
- **ZMovie Mode** — browse ZMOVIE.STR entries, edit subtitle text and timing, compile patched STR
- **Tab bar** — all-caps tabs (RADIO, DEMO, VOX, ZMOVIE) for quick mode switching

### Radio Editing
- Load RADIO.XML directly, or parse RADIO.DAT on-the-fly via RadioDatTools
- Edit subtitle text per VOX_CUES entry
- Split subtitle in two (halves timing, splits text on `\r\n` or midpoint)
- Delete subtitle
- Save RADIO.XML
- **This disc filter** — "This disc only" checkbox hides calls where any VOX_CUES has a zero block address (missing audio); checkbox only appears when such calls are present
- **Frequency filter** — filter codec calls by frequency in Radio mode

### Demo / VOX / ZMovie Editing
- **Original/Altered JSON split** — extracted data is stored as a read-only original; edits are tracked separately in a sparse "altered" JSON containing only modified entries. Unchanged entries are never touched during compile, preserving original Japanese text from recompiler encoding issues.
- **Altered entry markers** — entries with modifications are marked with a bullet (•) in the offset list for at-a-glance visibility
- **Revert to Original** — per-entry button to discard changes and restore the original extracted data; optional confirmation warning (configurable in Preferences > Editor)
- **Unclaimed VOX filter** — "Show unclaimed clips only" checkbox in VOX mode hides clips whose byte offset is referenced by a RADIO call; only appears when RADIO has been loaded
- Extract subtitle timing/text from DEMO.DAT, VOX.DAT, or ZMOVIE.STR without pre-splitting
- Edit start frame, duration, and text per subtitle entry
- Apply edits via copy-on-write: first edit to an entry copies it from original into altered, then mutates the altered copy
- Export dialogue to JSON file — exports only altered entries (Demo, VOX, ZMovie)
- Compile rebuilds entry by entry with 0x800 block alignment; entries can grow freely with offsets auto-adjusted
- ZMovie: patch-in-place compile back to ZMOVIE.STR via extractZmovie; raises clear error on subtitle overflow
- **Offsets JSON** — VOX and DEMO extraction builds an offsets.json mapping entry numbers to hex byte offsets for STAGE.DIR adjustment; saved in the .mtp project file

### Finalize Project
- **Finalize Project dialog** — batch-compile all (or selected) game data files in one step
- **Correct build order** — VOX compile → VOX offset adjust → DEMO compile → DEMO offset adjust → RADIO compile → ZMOVIE compile. Each step chains from the previous, ensuring STAGE.DIR and RADIO XML are fully patched before RADIO recompile.
- **Progress dialog** — real-time build output in a monospace log window with step-by-step status labels; replaces the old summary-only popup
- **VOX offset adjustment** — after VOX compile, automatically patches STAGE.DIR Pv tags and RADIO XML voxCode attributes with new block indices using `voxOffsetAdjuster.py`
- **DEMO offset adjustment** — after DEMO compile, automatically patches STAGE.DIR Ps/Pp tags with new demo offsets
- Per-format enable/disable via checkable group boxes (RADIO, DEMO, VOX, ZMOVIE)
- RADIO compile options: use original hex (`-x`), double-width save blocks (`-D`), Integral disc (`--integral`), debug output (`-v`)
- **STAGE.DIR controls** — input path (auto-detected), replace checkbox, output path with browse button (auto-populates from default output folder when unchecking replace)
- **STAGE.DIR disable warning** — warns user that disabling STAGE.DIR modifications will likely break the game if offsets changed
- **Output folder** — optional custom output directory; all files written with original names for easy repeated test builds. Overwrite checkbox (default on) for rapid iteration.
- **Default output folder** — configurable in Preferences > Build; auto-populates the Finalize dialog
- Summary appended to the progress log on completion showing per-format OK / FAILED status

### UI / UX
- **Persistent offset list** — QComboBox replaced with a scrollable QListWidget (~8 rows visible) for browsing entries without a dropdown
- **Navigation buttons** — ▲ Prev / ▼ Next buttons beside the offset list (Cmd+Up/Down shortcuts), vertically centered with frequency filter above
- **Index numbers** — optional 2-digit zero-padded indices on subtitle list and VOX cue list entries (toggle in Preferences > Editor)
- **Auto-select first subtitle** — selecting an entry in any mode automatically selects the first subtitle for immediate editing
- **Simplified File menu** — streamlined to Open Folder, Open Project, Save, Save As, Finalize, Quit
- **Title bar** — shows project filename when saved, "Unsaved Project" otherwise
- **App icon** — custom codec screen icon shown in window title bar and macOS dock/task switcher

### Audio Playback
- Convert VAG audio to WAV via ffmpeg, play via ffplay (off-thread)
- Subtitle overlay synced to playback using elapsed timer
- Stop button; graceful kill of in-flight conversion/playback subprocess
- Subtitle FPS estimation logged to console for tuning

### Preferences
- **Translation** — source/target language for Google Translate integration
- **Editor** — warn before revert toggle, show/hide index numbers toggle
- **Build** — default output folder for Finalize Project

### Font Editor (Tools > Font Editor)
- **Extract glyphs** — load STAGE.DIR and view all 440 kana/kanji glyph slots (12x12 2bpp) in a scrollable grid
- **Replace glyphs** — import single PNGs or batch-import from a folder (`glyph-NNN.png` naming convention)
- **Export glyphs** — export all 440 glyphs as individual PNGs for reference or external editing
- **Table file (.tbl)** — standard ROM hacking format mapping hex codes to characters; load, edit, and save `.tbl` files
- **Inject into STAGE.DIR** — write modified font back to STAGE.DIR (in-place patch at fixed offset)
- **Project integration** — `.tbl` is saved/restored with `.mtp` project files; encoder automatically uses `.tbl` overrides when compiling
- See [`FONT_EDITOR.md`](FONT_EDITOR.md) for full usage guide

### Setup / Distribution
- **Launch scripts** — `launch.sh` (macOS/Linux) and `launch.bat` (Windows); auto-setup on first run (venv, dependencies, submodule, ffmpeg)
- **Project restructure** — app source in `src/`, scripts submodule at root, clean top-level for end users
- **Dual remote** — GitLab (private) + GitHub (public) with single `git push`
- **GPL v3 license** — open source with community preservation addendum

---

## Planned / Wishlist

1. **VOX block length sync to RADIO.DAT** — when VOX subtitle edits change the block length of a clip, the corresponding VOX_CUES entries in RADIO.DAT need their block counts updated. The scripts submodule recompiler doesn't handle this yet. Requires changes in `mgs1-scripts` to read altered VOX lengths and patch RADIO.DAT/XML accordingly.

2. **Finalize Project end-to-end testing** — the Finalize Project dialog has not been thoroughly tested with real game data across all four formats (RADIO, DEMO, VOX, ZMOVIE). Needs a full round-trip test: load project → finalize → build ISO → verify in DuckStation.

3. **mkpsxiso integration** — after Finalize Project, optionally run mkpsxiso to produce a test ISO directly from the app, and launch it in DuckStation for immediate playback testing.

4. **ZMovie audio playback** — play the STR video stream alongside subtitle preview in ZMovie Mode, so timing edits can be verified against the actual FMV cutscene.

5. **RADIO.DAT executable patcher for --long mode** — the Japanese version requires a binary patch to the game executable to support the `--long` RADIO.DAT format (extended call lengths). Need to integrate a patcher that identifies the game version by SHA-256 hash and applies the correct patch. Should support multiple disc versions (JP, Integral, etc.) as part of a larger packaging/distribution flow.

## Known Bugs

1. **[scripts] Unknown chunk type crashes DEMO/VOX loading on Integral disc** — `demoClasses.py` line 637: `root.append("unknownChunk", {...})` passes two arguments to `ET.Element.append()` which only takes one. The Integral disc contains chunk types not seen in the original release, hitting the `case _:` fallback. Fix: wrap in `ET.Element()` — `root.append(ET.Element("unknownChunk", {...}))`. A local fix has been applied but needs to be committed to the `mgs1-scripts` submodule.
