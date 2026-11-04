"""The bundled frontend files stay in step with the manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "cozy_home"


def test_card_versions_match_manifest() -> None:
    version = json.loads((ROOT / "manifest.json").read_text())["version"]
    for name in (
        "cozy-home-master-card.js",
        "cozy-home-schedule-card.js",
        "cozy-home-rooms-card.js",
    ):
        source = (ROOT / "www" / name).read_text()
        match = re.search(r'const VERSION = "([^"]+)"', source)
        assert match, f"{name} has no VERSION constant"
        assert match.group(1) == version, f"{name} is {match.group(1)}, manifest is {version}"


def test_panel_imports_both_cards() -> None:
    source = (ROOT / "www" / "cozy-home-panel.js").read_text()
    assert "cozy-home-master-card.js" in source
    assert "cozy-home-rooms-card.js" in source
    assert "cozy-home-schedule-card.js" in source
    assert "cozy-home-panel-v" in source
