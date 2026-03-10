# MGS Qt UI — Feature List

## Implemented

### Project File (.mtp)
- **Open Folder** — scan a folder case-insensitively for RADIO.DAT, DEMO.DAT, VOX.DAT, ZMOVIE.STR and load all found at once
- **Open Project (.mtp)** — restore all subtitle edits and attempt to reload audio from stored DAT paths; prompts to relocate missing DATs
- **Save Project / Save Project As** — ZIP archive containing settings.json, radio.xml, demo-dialogue.json, vox-dialogue.json, zmovie-dialogue.json
- **Quick-access buttons** — Open Folder, Open Project, and Finalize Project buttons in the lower-right panel for one-click access without opening the menu

### Editor Modes
- **Radio Mode** — browse codec calls by offset, select VOX_CUES, edit subtitle text, play audio via VOX.DAT
- **Demo Mode** — browse DEMO.DAT entries with dialogue, edit subtitle text and timing, play audio with subtitle overlay
- **VOX Mode** — same as Demo mode but for VOX.DAT clips (independent subtitle/timing editing)
- **ZMovie Mode** — browse ZMOVIE.STR entries, edit subtitle text and timing, compile patched STR

### Radio Editing
- Load RADIO.XML directly, or parse RADIO.DAT on-the-fly via RadioDatTools
- Edit subtitle text per VOX_CUES entry
- Split subtitle in two (halves timing, splits text on `\r\n` or midpoint)
- Delete subtitle
- Save RADIO.XML
- **This disc filter** — "This disc only" checkbox hides calls where any VOX_CUES has a zero block address (missing audio); checkbox only appears when such calls are present

### Demo / VOX / ZMovie Editing
- **Unclaimed VOX filter** — "Show unclaimed clips only" checkbox in VOX mode hides clips whose byte offset is referenced by a RADIO call; only appears when RADIO has been loaded
- Extract subtitle timing/text from DEMO.DAT, VOX.DAT, or ZMOVIE.STR without pre-splitting
- Edit start frame, duration, and text per subtitle entry
- Apply edits to in-memory JSON (re-keyed on frame change)
- Export dialogue to JSON file (Demo, VOX, ZMovie)
- ZMovie: patch-in-place compile back to ZMOVIE.STR via extractZmovie; raises clear error on subtitle overflow

### Finalize Project
- **Finalize Project dialog** — batch-compile all (or selected) game data files in one step
- Per-format enable/disable via checkable group boxes (RADIO, DEMO, VOX, ZMOVIE)
- RADIO compile options: prepare lengths (`-p`), original hex (`-x`), double-width save blocks (`-D`), debug output (`-v`), STAGE.DIR input + output path (auto-detected from project folder)
- "Replace original files" option with overwrite confirmation warning
- Output naming: overwrite originals, or write `RADIO-NEW.DAT` / `DEMO-NEW.DAT` / `VOX-NEW.DAT` / `ZMOVIE-NEW.STR` / `STAGE-NEW.DIR` alongside originals
- Summary dialog on completion showing per-format OK / FAILED status

### Audio Playback
- Convert VAG audio to WAV via ffmpeg, play via ffplay (off-thread)
- Subtitle overlay synced to playback using elapsed timer
- Stop button; graceful kill of in-flight conversion/playback subprocess
- Subtitle FPS estimation logged to console for tuning

### Font Editor (Tools > Font Editor)
- **Extract glyphs** — load STAGE.DIR and view all 440 kana/kanji glyph slots (12x12 2bpp) in a scrollable grid
- **Replace glyphs** — import single PNGs or batch-import from a folder (`glyph-NNN.png` naming convention)
- **Export glyphs** — export all 440 glyphs as individual PNGs for reference or external editing
- **Table file (.tbl)** — standard ROM hacking format mapping hex codes to characters; load, edit, and save `.tbl` files
- **Inject into STAGE.DIR** — write modified font back to STAGE.DIR (in-place patch at fixed offset)
- **Project integration** — `.tbl` is saved/restored with `.mtp` project files; encoder automatically uses `.tbl` overrides when compiling
- See [`FONT_EDITOR.md`](FONT_EDITOR.md) for full usage guide

---

## Planned / Wishlist

1. **mkpsxiso integration** — after Finalize Project, optionally run mkpsxiso to produce a test ISO directly from the app, and launch it in DuckStation for immediate playback testing.

2. **ZMovie audio playback** — play the STR video stream alongside subtitle preview in ZMovie Mode, so timing edits can be verified against the actual FMV cutscene.
