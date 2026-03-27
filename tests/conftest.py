"""Shared fixtures for MGS round-trip integration tests."""
import sys, os
import pytest

# ── Path setup (mirrors mainwindow.py) ─────────────────────────────────────
PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

# Ensure src/ and scripts/ are importable
for p in [SRC_DIR, PROJECT_ROOT, os.path.join(PROJECT_ROOT, "scripts")]:
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def build_src_root():
    """Return the build-src/ directory path, skip the whole suite if missing."""
    root = os.path.join(PROJECT_ROOT, "build-src")
    if not os.path.isdir(root):
        pytest.skip(
            f"build-src/ not found at {root} — "
            "place game data there to run round-trip tests")
    return root


# Version configs: (folder_name, MGS_subfolder, finalize_flags)
VERSION_CONFIGS = [
    ("usa-d1",      "MGS", {"hex": True, "integral": False, "long": False}),
    ("jpn-d1",      "MGS", {"hex": True, "integral": False, "long": False}),
    ("integral-d1", "MGS", {"hex": True, "integral": True,  "long": False}),
]


def _version_ids():
    return [v[0] for v in VERSION_CONFIGS]


@pytest.fixture(params=VERSION_CONFIGS, ids=_version_ids())
def version_config(request, build_src_root):
    """Yield (version_name, data_folder, flags) for each game version."""
    folder_name, mgs_sub, flags = request.param
    data_folder = os.path.join(build_src_root, folder_name, mgs_sub)
    if not os.path.isdir(data_folder):
        pytest.skip(f"{data_folder} not found — skipping {folder_name}")
    return folder_name, data_folder, flags
