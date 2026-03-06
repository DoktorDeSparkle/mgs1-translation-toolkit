# MGS Undubbed GUI — Changes (claude-improvements branch)

## Summary

This branch adds full dialogue editing, binary round-trip serialization,
and cross-platform audio playback to the GUI.

---

### mainwindow.py

- **Subtitle editing panel** — three buttons added programmatically below the dialogue editor:
  - *Apply Edit* — writes text changes to the in-memory RADIO.XML element and
    timing changes (startFrame / displayFrames) to the corresponding VOX `dialogueLine`
  - *Split Subtitle* — splits the selected subtitle on `\r\n` (or at the midpoint
    if no line break exists), inserts a second entry in both the XML and the VOX demo,
    and halves the display duration across both halves
  - *Delete Subtitle* — removes the selected `SUBTITLE` element from the XML after
    a confirmation prompt
- **VOX timing display** — selecting a subtitle now populates `startFrameBox` and
  `durationBox` with the matching `dialogueLine` values from the loaded VOX.DAT
- **Async audio playback** — `VoxConversionThread` (QThread) runs the ffmpeg
  VAG→WAV conversion off the main thread so the window never freezes
- **Stop button** — added next to the Play button; cancels an in-progress
  conversion or stops `QMediaPlayer` mid-playback
- **Cross-platform audio** — replaced `ffplay` subprocess with `QMediaPlayer` +
  `QAudioOutput` (uses AVFoundation on macOS, WMF on Windows, GStreamer on Linux);
  no external `ffplay` binary required
- **File → Save VOX.DAT** — new menu action; performs a patch-in-place write of
  modified caption blocks back into a copy of the original VOX bytes
- **File → Save RADIO.XML** — wired to `radioDataEditor.saveXML()`
- **UI bug fixes**:
  - `startFrameBox` display base corrected from 9 → 10
  - `durationBox` maximum raised from 99 → 99 999 999
- `_loadingSubtitle` flag prevents spinbox signals from firing spurious
  "unsaved changes" indicators while data is being loaded into the editor
- Temp file paths now use `tempfile.gettempdir()` + `os.path.join()` for
  cross-platform compatibility

---

### scripts/radioModule.py

New methods on `radioDataEditor`:

| Method | Description |
|--------|-------------|
| `getSubElement(index)` | Returns the `SUBTITLE` `ET.Element` at the given index |
| `updateSubText(index, text)` | Updates the `text` attribute of the subtitle at index |
| `addSubtitle(index, text, after=True)` | Inserts a new `SUBTITLE` adjacent to the one at index, copying `face`/`anim` attributes from the sibling |
| `removeSubtitle(index)` | Removes the `SUBTITLE` at index |
| `saveXML(filename)` | Writes the current element tree to disk |

`xmlFilePath` instance variable added to track the loaded file path for save-as default.

---

### scripts/demoClasses.py

- **`dialogueLine.toBytes()`** (new) — serialises a subtitle line to bytes without
  the length prefix: `startFrame(4 LE) + displayFrames(4 LE) + buffer(4) + encoded_text + 4-byte padding`
- **`captionChunk._graphicsData`** — raw kanji/graphics bytes are now preserved
  during binary parse for lossless round-trip serialization
- **`captionChunk.toBytes()`** (replaced TODO stub) — full binary round-trip:
  rebuilds the subtitle block (all-but-last entries get a 4-byte LE length prefix;
  last entry gets a 4-byte zero marker), recalculates `dialogueLength` and the total
  chunk length, and appends the preserved `_graphicsData`
- **`demo.getModifiedBytes(originalBytes)`** (new) — patch-in-place strategy
  mirroring the CLI injector scripts: walks the original bytes chunk by chunk,
  replaces only `0x03` caption blocks with their serialized versions, and preserves
  audio/animation/EOF data verbatim; applies the same length-alignment rules as the
  CLI tools
- **Bug fixes in `toBytes()` methods**:
  - `audioChunk` / `demoChunk`: removed stray `+` operator (`+= + self.content` → `+= self.content`)
  - `audioChunk` / `demoChunk`: added missing `'little'` argument to `to_bytes(1)`
  - `demo.toBytes()`: fixed `self.items` → `self.segments`
  - `demo.toBytes()`: fixed `demoBytes // 0x800` → `len(demoBytes) // 0x800`

---

### scripts/audioTools/vagAudioTools.py

- Replaced hardcoded `"/tmp"` with `tempfile.gettempdir()` (cross-platform)
- All temp paths now use `os.path.join()` instead of string concatenation
- Temp files renamed to `mgs_vox_temp.*` for clarity
- Added `getTempWavPath()` helper so callers don't need to reconstruct the path
- `ffmpeg.run()` calls now pass `quiet=True` to suppress console noise
- `play_with_ffplay()` kept as a legacy CLI-only helper; it is no longer called
  from the GUI code path
- `playVagFile(convertOnly=True)` is the GUI entry point — converts to WAV and
  returns without playing; playback is handled by `QMediaPlayer` in the caller
