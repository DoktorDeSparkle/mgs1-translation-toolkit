# Font Editor — User Guide

The Font Editor lets translators replace the Japanese kana/kanji glyphs in Metal Gear Solid (PS1) with custom characters (e.g. accented Latin letters for European languages), and create a table file (`.tbl`) that tells the text encoder which byte codes map to which characters in the new font.

---

## Quick Start

1. Open the Font Editor from **Tools > Font Editor...**
2. Click **Load STAGE.DIR** and select your unmodified Japanese STAGE.DIR
3. You'll see all 440 glyph slots in a grid. Click any slot to inspect it.
4. Replace glyphs you need — either one at a time or batch from a folder
5. Update the character labels in the `.tbl` to match your new glyphs
6. Click **Apply to STAGE.DIR...** to write the modified font
7. Click **Save .tbl...** to save your character mapping

The `.tbl` file is automatically saved/restored with your `.mtp` project file.

---

## Concepts

### Glyph Slots

The MGS1 font has **440 fixed-size glyph slots** for kana and kanji characters. Each glyph is **12x12 pixels** at **2 bits per pixel** (4 grayscale levels: black, dark gray, light gray, white).

| Slots | Count | Original Contents |
|-------|-------|-------------------|
| 0 -- 82 | 83 | Hiragana (Japanese) |
| 83 -- 168 | 86 | Katakana (Japanese) |
| 169 -- 439 | 271 | Kanji and punctuation |

For a European language translation, you probably don't need any of these Japanese characters. You can freely replace any slot with your own glyphs.

### Byte Codes

Each glyph slot has a fixed 2-byte hex code that the game's text engine uses to display that character:

| Slot Range | Hex Code Range | Encoding |
|------------|----------------|----------|
| 0 -- 82 | `8101` -- `8153` | `0x81` + slot byte |
| 83 -- 168 | `8201` -- `8256` | `0x82` + slot byte |
| 169 -- 439 | `9001` -- `9110` | `0x90`/`0x91` + slot byte |

You don't need to think about these codes directly. The `.tbl` file maps them to readable characters for you.

### Table File (.tbl)

A `.tbl` file is a standard ROM hacking text format that maps hex codes to characters. Each line is `HEXCODE=CHARACTER`:

```
# Hiragana slots (repurposed for accented Latin)
8101=a
8102=b
8103=c
...
814a=A
814b=B

# Kanji slots (repurposed for accented Latin)
9002=a
9003=e
9004=i
9005=o
9006=u
9007=n
9050=A
9051=E
```

When you type `cafe` in the subtitle editor, the encoder looks up each character in the `.tbl` to find the matching hex code, then writes those bytes into the game data. The game then displays the glyph at that slot — which is your custom character if you've replaced it.

Lines starting with `#` are comments. Blank lines are ignored.

---

## Walkthrough: Adding Accented Characters for Portuguese

This example walks through adding `a`, `e`, `i`, `o`, `u`, `c`, `n` and their uppercase variants to the font.

### Step 1: Prepare Your Glyph Images

Create 12x12 pixel PNG images for each character you need. Rules:

- **Exactly 12x12 pixels** (images of other sizes will be scaled, but 12x12 gives best results)
- **Grayscale only** — use only these 4 values:
  - `#000000` (black / transparent)
  - `#555555` (dark gray)
  - `#AAAAAA` (light gray)
  - `#FFFFFF` (white / fully visible)
- Anti-aliasing is fine — the tool quantizes to the nearest of these 4 levels
- **Name them by slot number**: `glyph-000.png` through `glyph-439.png`

You only need to create PNGs for the slots you want to replace. Unchanged slots keep their original Japanese glyphs.

**Tip**: Export the original glyphs first (see below) to see the exact pixel grid and grayscale levels the game uses.

### Step 2: Export Originals as Reference

1. Open Font Editor, load your STAGE.DIR
2. Click **Export All Glyphs...**
3. Choose a folder — you'll get 440 individual PNGs

This gives you the original Japanese glyphs as a reference for sizing and positioning your custom characters.

### Step 3: Import Your Custom Glyphs

**Option A — One at a time:**
1. Click a slot in the grid
2. Click **Import PNG...**
3. Select your replacement image

**Option B — Batch from folder:**
1. Put all your `glyph-NNN.png` files in a folder
2. Click **Import Glyphs from Folder...**
3. Select that folder

Modified slots get a blue highlight in the grid so you can see what's changed.

### Step 4: Update the Character Labels

For each slot you replaced, update its character label:
1. Click the slot in the grid
2. In the detail panel on the right, edit the **Char** field
3. Type the character this glyph now represents (e.g. `a`)

This updates the `.tbl` mapping so the encoder knows that, for example, hex code `8101` now means `a` instead of `ぁ`.

### Step 5: Save Everything

1. **Save .tbl...** — saves your character mapping to a `.tbl` file
2. **Apply to STAGE.DIR...** — writes the modified glyphs into a new STAGE.DIR file

**Important**: Always save to a *new* STAGE.DIR rather than overwriting your original. Keep an unmodified backup.

### Step 6: Use in Your Project

The `.tbl` is automatically stored in your `.mtp` project file when you save. When you (or another translator) opens the project later, the `.tbl` is restored and the encoder uses it.

Now when you type subtitle text containing `a` or `e` in the subtitle editor, the Finalize step will encode them using the hex codes from your `.tbl`, and the game will display your custom glyphs.

---

## How the Encoder Uses the .tbl

When the `.tbl` is loaded, the encoder checks it **before** the default character lookups. The priority order is:

1. Fullwidth period (special case, `U+FF0E`)
2. **.tbl override** — if the character is in your table, use that hex code
3. ASCII (`< 0x80`) — standard single-byte encoding
4. Spanish/accented chars — built-in extended Latin support
5. Hiragana, Katakana, Punctuation, Kanji — default Japanese dicts

This means your `.tbl` overrides take priority. If you map `a` to `9050` in your `.tbl`, the encoder will use `9050` instead of the normal ASCII `a` (which is a single byte `0x61`). Choose carefully whether you want to override standard ASCII characters or only add new non-ASCII ones.

**Tip for European languages**: You likely don't need to remap standard ASCII (A-Z, a-z, 0-9, punctuation) — the game already has those in its built-in font. You only need `.tbl` entries for characters that *don't* exist in the standard font, like `a`, `e`, `n`, `u`, etc.

---

## File Formats

### Glyph PNG Files

- 12x12 pixels, grayscale
- Named `glyph-NNN.png` where NNN is the zero-padded slot index (000-439)
- Only the slots you want to replace need PNG files

### .tbl File

Plain text, UTF-8 encoded:

```
# MGS1 Font Table File
# Format: HEXCODE=CHARACTER

# Hiragana range
8101=ぁ
8102=あ
8103=ぃ
...

# Katakana range
8201=ァ
8202=ア
...

# Kanji/punctuation range
9001=
9002=、
9003=。
...
```

### STAGE.DIR

The font is patched in-place at a fixed offset (`0x565DF8`) within STAGE.DIR. The tool reads the entire file, overwrites the 15,840-byte kana/kanji region, and writes a new file. No other data in STAGE.DIR is modified.

---

## Limitations

- **440 slots maximum** — you cannot add more glyph slots than the game's font table supports. Plan your character set within this budget.
- **12x12 pixels only** — all glyphs are fixed 12x12. There is no variable-width support for this section of the font.
- **4 grayscale levels** — the 2bpp format only supports black, dark gray, light gray, and white. Full-color or high-depth grayscale images are quantized.
- **ASCII font is separate** — the standard ASCII characters (space through `~`, 96 characters) use a different variable-width section of the font not handled by this tool. See the [font_manipulator](../mgs-font-hacking/README.md) tool for ASCII font editing.
- **RADIO.DAT inline glyphs** — some rare kanji are embedded directly in individual codec calls rather than the main font. These are not affected by STAGE.DIR font replacement.
- **Japanese disc only** — the font offset (`0x565DF8`) is specific to the Japanese PSX disc image. Other regional versions may have the font at a different offset.
