import os, subprocess
import sys
import re
import xml.etree.ElementTree as ET

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


def getVoxFile(voxOffset: str, filename: str = None, path: str = None):
    global voxManager
    if filename is None:
        filename = "tempFile.vag"
    print(f"writing Vox {voxOffset} to {path}/{filename}")
    # Write the file to temp
    voxData = voxManager.get(str(voxOffset))
    vagFile = voxCtl.outputVagFile(voxData, filename, path)
    VAG.playVagFile(vagFile, convertOnly=True)
    # For now based on that file this is a static output, will change later.
    return "/tmp/temp.wav"


def transcribeAudio(filename: str) -> list: 
    response = []
    text = subprocess.run(["whisper-cli", "-l", "ja", "--no-prints", "-m", "whisper-depends/ggml-large-v3-turbo.bin", filename], capture_output=True)
    result = text.stdout.decode("utf8")
    print(result)

charactersFound = open("charactersFound.txt", 'w')

# Test for gathering audio and subtitles:
unknownCharPattern = re.compile(r'\[[a-f0-9]{72}\]')
for voice in RadioData.findall(".//VOX_CUES"):
    clipOffset = voice.get("content")[8:16]
    print(f">>> Vox Cue: {clipOffset}")
    for subtitle in voice.findall(".//SUBTITLE"):
        text = subtitle.get('text').replace("\\r\\n", "")
        print(text)
        # Find unknown character sequences
        matches = unknownCharPattern.findall(text)
        for match in matches:
            charactersFound.write(match + '\n')
    # Remember some are stored on disk 2!
    if clipOffset == "00000000":
        pass

charactersFound.close()

# Test for whisper:
# print(f'Now running whisper-cli...')
# transcribeAudio("/tmp/temp.wav")