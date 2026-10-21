# Reforged Studio - user guide

Reforged Studio edits Assassin's Creed III multiplayer: its maps and its characters. It reads the game you own,
shows everything in 3D, builds your changes into test files, installs them into the game and puts the originals back.
It also turns singleplayer areas into new multiplayer maps.

**F1** (Help > Keyboard shortcuts) lists every key.

## Setting up

The first start asks for:

* **the game's multiplayer folder** - the one holding `DataPC.forge` (`...\Assassin's Creed III\multi`);
* **the singleplayer folder** (optional) - the install's main folder, holding `DataPC_DX11.forge`; needed for
  singleplayer models and areas;
* **a workspace** - where builds, caches and the game's original files are kept (it grows to tens of GB: a character
  build is a full copy of the 6 GB DataPC);
* **Blender** (optional) - for editing character meshes.

The Studio then reads the game once (its maps, its characters and the ids in use - a few minutes). **File >
Settings...** changes any of this later.

**Your game files are safe.** Nothing in the game folder changes until you press **Install** or **Test in game**.
Before a file is first replaced, the game's own copy is kept in `<workspace>\originals`, and **Put back** copies it
in again. Builds always start from those originals, never from an installed build.

The menu bar switches between the two workspaces: **Maps** and **Characters**.

## Maps

### Opening and looking around

* **Maps** (left): double-click a map. The first open of a map takes a few seconds; after that it opens at once.
  The list follows the game folder: a map file added, replaced or removed there shows up (or goes) by itself
  within a few seconds, and the Library indexes a new map's models in the background.
* **Viewport**: hold the **right mouse button** and use **W A S D** to fly (Q / E down / up, Shift faster, the wheel
  sets the speed). Middle-drag pans, **Alt + left drag** orbits, **F** frames the selection, **Home** the whole map,
  **T** looks straight down.
* **Show** menu: collision, climb edges, blend groups, the navmesh, crowd routes, markers, volumes, spawn labels.
* **Game mode** (toolbar): shows one mode's objects - its spawn points (green: respawns, yellow-green: match starts),
  its boundaries and its mode objects.

### Selecting and editing

* **Click** to select (Shift / Ctrl + click adds), or **drag a box** from empty space.
* **B** selects a whole building from any of its pieces. **G** adds every piece touching the selection, **Shift+G**
  keeps growing until nothing more touches (capped by *Largest structure* in the Select menu).
* **W** move, **E** turn, **Tilt** turns on every axis; **World / Local** and **Snap** on the toolbar. The Inspector's
  fields edit exactly. **End** drops the selection onto what is under it.
* **Ctrl+D** copies, **Delete** removes, **Ctrl+R** puts an object back, **Ctrl+Z / Ctrl+Y** undo / redo.
* **Edit > Clear the map** removes every visible object (keeping the ground, spawns, game modes and other invisible
  systems) so you can build a new layout; **Edit > Clear an area** does the same inside an outline you click.

Moved and removed buildings take their plot of ground with them, and every build looks for holes left behind
(somewhere you'd fall through, or see through to the water) and fills them.

### The Library

**L** opens the Library: every model of every map in your game folder, filed by category (houses, walls, stairs,
market stalls, crates, trees ...). Right-click a model to file it under another category or one of your own.

1. Find a model by name, category or map; click it to see it turning, with its size and materials.
2. Look at the spot in the viewport, then **double-click** the model: it stands where the camera looks.
3. Move and turn it like any object. Its **Solid** box gives it collision and climb edges; **Climbing** chooses
   *as in its own map*, *climbable everywhere*, *climbable outside* (handholds on outer walls only) or *not climbable*.

**+ Wall / + Box / + Slab** (Add menu) adds a simple shape in any material from the Library (pick it with **Use**
under a model's preview). Set its size in the Inspector.

**Blend groups** (the NPC groups players blend into: merchants, drinkers, wall leaners ...) and **gameplay objects**
(hiding spots, benches, chase breakers, lifts) have Library tabs of their own. A merchant comes with its stall.

### My assets

Save anything you build for reuse:

* select its parts (a building, copies, placed models, walls) and use **Edit > Save selection as an asset...**;
* **Edit > Make asset from building...** turns a building into an asset whose windows, doors and props are assets of
  their own;
* place an asset from **My assets** like a model. One click picks the whole asset, a double-click goes inside it to one
  part, **Esc** comes back out. **Apply this placement's changes to the asset** saves them for later placements;
* **Open in the asset editor** edits an asset on an empty grid.

Assets are kept as small files in `<workspace>\assets` and work on any map.

### Ground

* **Ground > Raise / lower an area**: click the area's corners, **Enter** to finish, then set its height (-20 to
  +20 m). What stands wholly inside moves with it; edges get walls.
* **Ground > Sculpt**: click to raise, **Shift+click** to lower, drag to paint; set the brush's radius and height.
* **Scatter (P)**: paint copies of the selected objects (or a Library model) over the ground, each at a random turn,
  set down on the ground. **Shift+drag** removes them.
* **Ground > Remake the navmesh in an area** / **Keep crowds out of an area**: rebuild where crowds can walk, or keep
  them out. Click a crowd route to switch it on or off and set its width.

### The Map panel

* **Lighting**: the map's time-of-day table, with controls for the sun, shade, fog, colour grade, the Assassin filter
  and the sky, and presets. The Studio's viewport can't show the game's lighting: judge it in game.
* **Kill height** (players below it die) and **view distance**.
* **Spawn points** for the shown game mode: move, turn, copy, add, remove.
* **Boundaries**: drag the out-of-bounds outline's corners, insert or remove corners.

The Studio checks spawns and boundaries as you edit (every mode keeps its respawns and match starts, inside its
boundary, on the ground). Errors stop a build.

### Building and testing

* **Ctrl+B** builds the map into your workspace and reads the result back to check it. The Build tab lists what came
  in and every check.
* **Install** puts the build into the game; **Put back** restores the original.
* **F5 - Test in game** builds if needed, installs, starts the game and puts the original back when the game closes.
* **V - Walk mode**: walk the edited map as a player-sized capsule (run, jump, climb onto ledges in reach) to judge
  spacing and jump gaps before a build. It isn't AC3's movement: test in game for free-running.
* **File > Save project** (Ctrl+S) saves your edits as a `.ac3proj` file; **Open project** brings them back.

### Singleplayer

With the singleplayer folder set:

* **Singleplayer models**: the Library's map list has a *Singleplayer* part (Boston, New York, the Frontier,
  Homestead ...). Choose one and its models are listed like any map's (the first read takes a few minutes).
* **A singleplayer area as a new map**: **File > New map from a singleplayer area**. Choose a singleplayer map, drag
  the area (20 - 160 m a side), check its preview and street level, pick the host map whose systems it takes over, and
  **Convert**. Minutes later the new map is in the Maps list under *From singleplayer areas*: open it and edit it like
  any other. Its conversion is checked (climbing, meshes, holes, boundaries, spawn points) and the results are shown.

## Characters

* **Personas** (left): every character, by game. Click one to open it.
* **Parts** (left, below): the look, the colour scheme, each slot's part and its materials, the weapon. The box beside
  a part shows or hides it.
* **Character** (centre): click a part to pick it; drag to turn, wheel to zoom. **Play idle** animates the character;
  the weapon can be drawn in the hand, in its holster or beside the character.
* **Inspector** (right), for what is picked:
  * a **part**: its colours, its materials, its **mesh** - *Edit in Blender* (the Studio opens it, and picks up the
    saved edit) or *Import a .glb...*, and whether it goes in as a **new look** (its own name and description)
    instead of replacing the stock one;
  * a **material**: a lit preview and every value (specular, fresnel, tint ...);
  * the **colour scheme**: every garment colour;
  * the **weapon**: swap its model for another of the same class, or add it as a new weapon.
* **Changes** (bottom): every change in one list, with undo; **Build** (Ctrl+B) makes a test DataPC; **Test in game**
  (F5) builds, installs, starts the game and puts the original back when it closes.
* **Test builds** (bottom): your builds, to install or delete.

## Files

* Settings: `%APPDATA%\Reforged Studio\config.json` (File > Settings changes it).
* Workspace (your builds, caches, the kept originals): chosen at setup, `Documents\Reforged Studio` by default.
* Window layout: `studio\build\studio_layout.ini` (delete it for the default layout).
* Your assets: `<workspace>\assets`.

## Troubleshooting

* **"No DataPC.forge in that folder"**: pick the game's `multi` folder.
* **No singleplayer maps listed**: set the singleplayer folder in File > Settings to an install that has
  `DataPC_DX11.forge`.
* **"can't start the engine"**: run `setup.ps1` again (it makes the `.venv` the Studio runs on), or set
  `REFORGED_STUDIO_PYTHON` to a Python 3.10+ with numpy and Pillow.
* **Test in game refuses**: close the game first; a map can't be swapped while it runs.
* **A game file "is owned by Administrators"**: the message names the one-time command that gives the file back to
  your account.
