# Reforged Studio

A desktop editor for **Assassin's Creed III multiplayer**: its maps (move, copy and remove buildings and props, place
models from any map, raise and sculpt ground, the crowds' navmesh, blend groups, spawn points, boundaries, lighting,
gameplay objects) and its characters (colour schemes, material values, weapons, part meshes through Blender, new
looks and weapons) - previewed in 3D, built into test files and installed into the game in one click, and put back.

**It ships no game files.** It reads the Assassin's Creed III installation you own and writes its builds to your own
workspace. Made for an **acmpreforged** install (every persona and ported map it knows); a plain AC3 install works for
AC3's own maps and personas.

## Install

Needs Python 3.10+ and MSVC (x64). Then:

```powershell
.\setup.ps1                  # .venv + requirements.txt + build
studio\start_studio.cmd
```

The first start asks for the game's multiplayer folder (the one with `DataPC.forge`), a workspace (builds and kept
originals; tens of GB), and optionally the singleplayer install and Blender. **File > Settings...** changes them later.

## Safety

The Studio never changes a game file until you press **Install** (or *Test in game*). Before it first writes over a
file it keeps the game's own copy in `<workspace>\originals`; **Put back** / *Restore the original* copies that back.
Builds start from those originals, never from a Studio build that is installed.

## Using it

The menu bar switches between the **Maps** and **Characters** workspaces. [`studio/GUIDE.md`](studio/GUIDE.md) is the
user guide; **Help > Keyboard shortcuts** (F1) lists the keys.

## Licence

GPL v3 or later (`LICENSE`); `NOTICE.md` lists the parts from elsewhere. Assassin's Creed is a trademark of Ubisoft
Entertainment; this project is not affiliated with Ubisoft.
