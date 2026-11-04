"""The interface's textures: which belong to the interface and what the data says about each
(ui/catalogue.py), their pictures (ui/extract.py) and the search's ranking (ui/search.py), on a
storage made up here; and the catalogue from the game's own data (marker game_data)."""

import fnmatch
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from heroes_capture.common.dds import clear_dds, solid_dds
from heroes_capture.ui import catalogue, extract, search

LAYOUT = """<?xml version="1.0" encoding="utf-8"?>
<Desc>
  <Frame type="Frame" name="TalentPanel">
    <Frame type="Image" name="Background">
      <Texture val="@UI/Storm_Talent_Panel_BG" layer="0"/>
      <TextureType val="NineSlice"/>
      <Tiled val="true"/>
    </Frame>
    <Frame type="Button" name="Close">
      <Tooltip val="@UI/CloseTooltip"/>
      <StateGroup name="Hover"><Texture val="Assets\\Textures\\storm_ui_close_hover.dds"/></StateGroup>
    </Frame>
  </Frame>
</Desc>"""

BUTTONS = """<?xml version="1.0" encoding="utf-8"?>
<Catalog>
  <CButton id="ButcherEnraged"><Icon value="Assets\\Textures\\storm_ui_icon_butcher_enraged.dds"/></CButton>
  <CActorModel id="Rock"><Texture value="Assets\\Textures\\rock_diffuse.dds"/></CActorModel>
</Catalog>"""


class Storage:
    """A storage of files given here, found by the same wildcards CascLib takes."""

    def __init__(self, files: dict[str, bytes | str]):
        self.files = {name: data.encode() if isinstance(data, str) else data for name, data in files.items()}

    def find(self, mask: str) -> list[str]:
        return [n for n in self.files if fnmatch.fnmatch(n.lower(), mask.lower())]

    def read(self, name: str) -> bytes | None:
        return self.files.get(name)

    def build(self) -> int:
        return 12345


TEXTURES = "mods\\heroes.stormmod\\base.stormassets\\assets\\textures"
STORAGE = {
    f"{TEXTURES}\\storm_talent_panel_bg.dds": solid_dds((20, 30, 80), 64, 32),
    f"{TEXTURES}\\storm_ui_close_hover.dds": clear_dds(16, 16),
    f"{TEXTURES}\\storm_ui_icon_butcher_enraged.dds": solid_dds((200, 40, 20), 32, 32),
    f"{TEXTURES}\\rock_diffuse.dds": solid_dds((90, 90, 90), 32, 32),
    "mods\\heroes.stormmod\\enus.stormassets\\assets\\textures\\storm_ui_close_hover.dds": solid_dds((255, 255, 255), 16, 16),
    "mods\\heroesdata.stormmod\\base.stormdata\\gamedata\\assets.txt": "UI/Storm_Talent_Panel_BG=Assets\\Textures\\storm_talent_panel_bg.dds\n",
    "mods\\heroes.stormmod\\base.stormdata\\ui\\layout\\talents.stormlayout": LAYOUT,
    "mods\\heroesdata.stormmod\\base.stormdata\\gamedata\\buttondata.xml": BUTTONS,
    "mods\\heroesdata.stormmod\\enus.stormdata\\localizeddata\\gamestrings.txt": "Button/Name/ButcherEnraged=<c val=\"FF0000\">Enraged</c>\nUnit/Name/ButcherEnraged=Not a button\n",
}


def test_the_interface_s_textures_and_what_the_data_says_about_them():
    found = catalogue.catalogue(Storage(STORAGE))
    assert sorted(found) == ["storm_talent_panel_bg.dds", "storm_ui_close_hover.dds", "storm_ui_icon_butcher_enraged.dds"]  # not the rock's
    panel = found["storm_talent_panel_bg.dds"]
    assert panel["aliases"] == ["UI/Storm_Talent_Panel_BG"]
    assert panel["layouts"] == [{"layout": "talents", "frame": "TalentPanel/Background", "field": "Texture", "type": "NineSlice", "tiled": True}]
    close = found["storm_ui_close_hover.dds"]
    assert close["path"].split("\\")[2] == "enus.stormassets"  # the English one over the shared one
    assert close["layouts"] == [{"layout": "talents", "frame": "TalentPanel/Close", "field": "Texture"}]  # not its tooltip
    assert found["storm_ui_icon_butcher_enraged.dds"]["data"] == [{"entry": "CButton ButcherEnraged", "field": "Icon", "name": "Enraged"}]


def test_extraction_writes_each_picture_and_reuses_unchanged_ones(tmp_path: Path):
    storage = Storage(STORAGE)
    found = extract.extract(storage, tmp_path)["textures"]
    panel = found["storm_talent_panel_bg.dds"]
    assert (panel["width"], panel["height"], panel["alpha"], panel["format"]) == (64, 32, False, "DXT1")
    assert Image.open(tmp_path / panel["png"]).size == (64, 32)
    assert Image.open(tmp_path / panel["thumb"]).size == (64, 32)  # small already: not enlarged
    (tmp_path / "png" / "gone.png").write_bytes(b"")  # a texture the game no longer has
    written = (tmp_path / panel["png"]).stat().st_mtime_ns
    storage.files[f"{TEXTURES}\\storm_ui_icon_butcher_enraged.dds"] = clear_dds(8, 8)
    again = extract.extract(storage, tmp_path)["textures"]
    assert (tmp_path / panel["png"]).stat().st_mtime_ns == written
    assert again["storm_ui_icon_butcher_enraged.dds"]["alpha"] is True
    assert not (tmp_path / "png" / "gone.png").exists()


def test_texture_formats():
    assert extract.texture_format(solid_dds((0, 0, 0), 4, 4)) == "DXT1"
    assert extract.texture_format(clear_dds(4, 4)) == "DXT5"


class Model:
    """The image-text model's place: every picture and the description the same vector."""

    def text(self, description: str) -> np.ndarray:
        return np.array([1.0, 0.0])


def test_the_description_s_words_in_the_data_add_to_a_picture_s_score():
    textures = {"a.dds": {"aliases": ["UI/Talent_Background"]}, "b.dds": {"data": [{"entry": "CButton X", "name": "Talent"}]},
                "c.dds": {}}
    vectors = np.array([[0.1, 0.0], [0.1, 0.0], [0.12, 0.0]])
    ranked = search.rank("the talent background", ["a.dds", "b.dds", "c.dds"], vectors, textures, Model())
    assert [name for name, _ in ranked] == ["a.dds", "b.dds", "c.dds"]
    assert ranked[0][1] == pytest.approx(0.1 + search.WORD_WEIGHT)


@pytest.mark.game_data
@pytest.mark.xdist_group("cdn")
def test_the_game_s_interface_catalogue(storage):
    found = catalogue.catalogue(storage)
    assert len(found) > 10000
    assert {"entry": "CButton ButcherEnragedHotbar", "field": "Icon", "name": "Enraged"} in found["storm_ui_icon_butcher_enraged.dds"]["data"]
    well = found["storm_ui_ingame_healingwell_active_borderglow.dds"]
    assert well["aliases"] == ["UI/Storm_UI_Ingame_HealingWell_Active_BorderGlow"]
    assert any(u["layout"] == "heroconsole" and u["frame"].endswith("/HealingGlowImage") for u in well["layouts"])
