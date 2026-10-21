# Notices

Reforged Studio is free software under the **GNU General Public License, version 3 or later** (LICENSE).

**No game data is included.** The Studio reads the `.forge` archives of an Assassin's Creed III installation the user
owns, at the folder the user points it at, and writes its builds to the user's own workspace. Assassin's Creed is a
trademark of Ubisoft Entertainment; this project is not affiliated with or endorsed by Ubisoft.

## Code this project includes

| Part | Where | Licence |
|---|---|---|
| Dear ImGui | `studio/third_party/imgui` | MIT (`studio/third_party/imgui/LICENSE.txt`) |
| ImGuizmo | `studio/third_party/ImGuizmo` | MIT (`studio/third_party/ImGuizmo/LICENSE`) |
| nlohmann/json | `studio/third_party/nlohmann` | MIT (`studio/third_party/nlohmann/LICENSE.MIT`) |
| LZO2A / LZO1X decoder / encoder | `forgelib/datafile.py`, `forgelib/lzo1x.py`, `forgelib/native/ac3lzo.c` (built by `setup.ps1`) | a Python and a C expression of liblzo2's published algorithms (lzo-2.10, Markus F.X.J. Oberhumer), GPL v2 or later - the reason this project is GPL |
| forgelib | `forgelib/` | this project's own (GPL v3 or later): the AC3 archive, map and character library |
| ac3codec | `ac3codec/` | this project's own (GPL v3 or later): the FastLoad object codec, and the object schema read from the user's own AC3MP.exe |

## Code this project fetches at setup (not bundled)

| Part | Source | Note |
|---|---|---|
| numpy, Pillow | PyPI | their own licences |

## Thanks

**anvilforge-py** (https://github.com/ACBMP/anvilforge-py), by Patrick Munnich, was the reference
ac3codec was checked against, object by object, and an inspiration for it - used with his permission.
The Studio ships none of its code or data.
