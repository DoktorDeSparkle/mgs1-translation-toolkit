import os, subprocess
import sys
import re
import xml.etree.ElementTree as ET
import numpy as np
import matplotlib.pyplot as plt

# Add scripts directory to path so internal imports work
script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Scripts
from scripts import demoManager as DM
import scripts.demoClasses as voxCtl
voxManager: dict [str, voxCtl.demo] = {} # TODO: Fix pathing for scripts.
import scripts.audioTools.vagAudioTools as VAG

# Get the Radio ET set up first
# Data files are in parent directory
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
radioXmlFile = os.path.join(base_dir, 'jpn', 'RADIO.xml')
radioFile = open(radioXmlFile, 'r')
RadioData = ET.parse(radioFile)

# Vox data
voxFile = os.path.join(base_dir, 'jpn', 'VOX.DAT')
voxData = open(voxFile, 'rb').read()
voxManager = DM.parseDemoFile(voxData)


def getVoxFile(voxOffset: str, filename: str = None, path: str = None) -> str:
    """Extract VOX audio to WAV file, returns path to the WAV."""
    global voxManager
    if filename is None:
        filename = "tempFile.vag"
    if path is None:
        path = "/tmp"
    # Convert hex offset to actual byte address: hex_value * 0x800
    actualOffset = int(voxOffset, 16) * 0x800
    print(f"Writing Vox {voxOffset} (offset {actualOffset}) to {path}/{filename}")
    # Write the file to temp
    voxData = voxManager.get(str(actualOffset))
    if voxData is None:
        print(f"WARNING: No VOX data found for offset {voxOffset}")
        return None
    vagFile = voxCtl.outputVagFile(voxData, filename, path)
    VAG.playVagFile(vagFile, convertOnly=True)
    return "/tmp/temp.wav"


def transcribeAudio(filename: str) -> str:
    """Run whisper-cli on an audio file and return the transcription."""
    import shutil
    if filename is None or not os.path.exists(filename):
        return ""
    whisper_model = "./whispermodels/ggml-large-v3-turbo.bin"

    # Find whisper-cli in PATH
    whisper_bin = shutil.which("whisper-cli")
    if whisper_bin is None:
        print("ERROR: whisper-cli not found in PATH")
        return ""

    # Run with inherited environment
    result = subprocess.run(
        [whisper_bin, "-l", "ja", "--no-prints", "--output-txt", "-m", whisper_model, filename],
        capture_output=True,
        env=os.environ.copy()
    )

    if result.returncode != 0:
        print(f"Whisper error (exit {result.returncode}): {result.stderr.decode('utf8')}")
        return ""

    textOnly = open("/tmp/temp.wav.txt", "r").read()
    print(textOnly)

    return textOnly


def extractKnownChars(text: str, unknownPattern: re.Pattern) -> str:
    """Remove unknown hex sequences from text to get known characters only."""
    return unknownPattern.sub("", text).replace("\\r\\n", "").replace(" ", "")


def showCharacterGraphic(hex_string: str):
    """Display a character image from the 72-char graphics hex (non-blocking)."""
    file_data = bytes.fromhex(hex_string)

    # Convert binary data to bit string
    bit_string = ''.join(format(byte, '08b') for byte in file_data)

    # Fixed 12x12 grid
    width, height = 12, 12

    # Convert bit string to 2D pixel array
    pixel_grid = np.zeros((height, width), dtype=np.uint8)

    for i in range(len(bit_string) // 2):
        x, y = i % width, i // width
        bits = bit_string[i * 2 : i * 2 + 2]

        if bits == "00":
            pixel_grid[y, x] = 0     # Black
        elif bits == "01":
            pixel_grid[y, x] = 85    # Dark gray
        elif bits == "10":
            pixel_grid[y, x] = 170   # Light gray
        else:
            pixel_grid[y, x] = 255   # White

    # Display image (non-blocking so user can still type)
    plt.figure(figsize=(2, 2))
    plt.imshow(pixel_grid, cmap="gray", interpolation="nearest")
    plt.axis("off")
    plt.title("Unknown Character")
    plt.show(block=False)
    plt.pause(0.1)  # Small pause to ensure window renders


def formatSubtitleDisplay(text: str) -> str:
    """Format subtitle text with line breaks after punctuation for readability."""
    # Protect placeholders from punctuation replacement
    text = text.replace("[???]", "\x00TARGET\x00")
    text = text.replace("[?]", "\x00OTHER\x00")

    # Add line break after sentence-ending punctuation
    for punct in ['。', '？', '！', '、']:
        text = text.replace(punct, punct + '\n')

    # Restore placeholders
    text = text.replace("\x00TARGET\x00", "[???]")
    text = text.replace("\x00OTHER\x00", "[?]")
    return text


def matchCharacters(subtitleText: str, transcription: str, hexSequences: list) -> tuple[dict, bool]:
    """
    Prompt user to match unknown hex sequences to kanji from whisper transcription.
    Human-in-the-loop matching for accuracy.

    Returns tuple of (matches dict, quit_flag).
    quit_flag is True if user entered 'q' or 'quit'.
    """
    matches = {}

    if not transcription or not hexSequences:
        return matches, False

    # Clean the subtitle text for display (replace hex with placeholder)
    hexPattern = re.compile(r'\[[a-f0-9]{72}\]')

    for hexSeq in hexSequences:
        hexKey = hexSeq[1:-1]  # Strip brackets

        # Show subtitle with the unknown character highlighted
        displayText = subtitleText.replace("\\r\\n", " ")
        displayText = displayText.replace(hexSeq, "[???]")
        # Collapse other hex sequences for readability
        displayText = hexPattern.sub("[?]", displayText)
        # Format with line breaks after punctuation
        displayText = formatSubtitleDisplay(displayText)

        print(f"\n  --- Character Identification ---")
        print(f"  Subtitle:\n{displayText}")
        print(f"  Whisper:\n{transcription}")
        print(f"  Hex: {hexKey[:32]}...")

        # Show the character graphic
        showCharacterGraphic(hexKey)

        # Prompt user for the character
        user_input = input("  Enter matching character (or 'q' to quit): ").strip()

        # Close the preview window
        plt.close()

        # Check for quit
        if user_input.lower() in ('q', 'quit'):
            print("  -> Saving and quitting...")
            return matches, True

        if user_input and len(user_input) == 1:
            matches[hexKey] = user_input
            print(f"  -> Matched: '{user_input}'")
        elif user_input and len(user_input) > 1:
            print(f"  -> Skipped (enter only 1 character)")
        else:
            print(f"  -> Skipped")

    return matches, False


# Track unique unknown hex sequences we've already processed
processedHex = set()
# Dict to store identified characters: {hex_string: kanji}
identifiedCharacters = {}

# Regex for unknown character sequences (72 hex chars = 36 bytes per kanji glyph)
unknownCharPattern = re.compile(r'\[[a-f0-9]{72}\]')

print("=== MGS1 Unknown Character Identifier ===")
print("Scanning VOX_CUES for unknown characters...\n")

shouldQuit = False
for voice in RadioData.findall(".//VOX_CUES"):
    clipOffset = voice.get("content")[8:16]

    # Skip null offsets (stored on disk 2)
    if clipOffset == "00000000":
        continue

    # Collect all subtitles and their unknown hex sequences for this VOX cue
    allText = ""
    allUnknowns = []

    for subtitle in voice.findall(".//SUBTITLE"):
        text = subtitle.get('text', '')
        allText += text
        matches = unknownCharPattern.findall(text)
        for match in matches:
            hexKey = match[1:-1]  # Strip brackets
            # Skip if already processed or identified
            if hexKey not in processedHex and hexKey not in identifiedCharacters:
                allUnknowns.append(match)
                processedHex.add(hexKey)

    # If we found new unknown characters, process this clip
    if allUnknowns:
        print(f"\n>>> VOX Cue: {clipOffset}")
        print(f"    Found {len(allUnknowns)} new unknown character(s)")
        print(f"    Subtitle: {allText.replace(chr(92) + 'r' + chr(92) + 'n', ' ')[:80]}...")

        # Extract and convert audio
        wavFile = getVoxFile(clipOffset)

        if wavFile and os.path.exists(wavFile):
            # Transcribe with Whisper
            print(f"    Transcribing...")
            transcription = transcribeAudio(wavFile)
            print(f"    Whisper: {transcription}")

            # Try to match characters
            newMatches, shouldQuit = matchCharacters(allText, transcription, allUnknowns)
            identifiedCharacters.update(newMatches)

            if shouldQuit:
                break

    if shouldQuit:
        break

# Write results
print(f"\n=== Results ===")
print(f"Processed {len(processedHex)} unique unknown hex sequences")
print(f"Identified {len(identifiedCharacters)} characters")

with open("charactersFound.txt", 'w', encoding='utf-8') as f:
    f.write("# Identified Characters\n")
    f.write("# Format: hex_graphics_data -> kanji\n\n")

    if identifiedCharacters:
        f.write("identified = {\n")
        for hexKey, kanji in identifiedCharacters.items():
            f.write(f'    "{hexKey}": "{kanji}",\n')
        f.write("}\n\n")

    f.write("# Unidentified hex sequences (still need manual review):\n")
    unidentified = [h for h in processedHex if h not in identifiedCharacters]
    for hexKey in unidentified:
        f.write(f"# [{hexKey}]\n")

print(f"\nResults written to charactersFound.txt")
