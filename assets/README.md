# My assets

User assets saved by Reforged Studio: Library > My assets, Edit > Save selection as an asset, Edit > Make asset from
building, and the asset editor's Save. There is one JSON file per asset, and its file name (without `.json`) is the
asset's **id**. No game data is stored here: a part names its source model (a map file name + a model uid, read
from the release's map when the asset is used), a primitive, or **another asset**.

```json
{"format": "ac3studio-asset/2", "name": "Barracks (nested)", "made_on": "Fort Wolcott", "saved": "<date>",
 "parts": [
  {"id": "p0", "model": {"map": "DataPC_AC3MP_Fort_Wolcott.forge", "archive": "<path when saved>", "mesh": "<uid hex>"},
   "name": "AC3MP_FORT_Barracks_building_01A_001/14", "solid": true, "matrix": [16 floats, row-vector]},
  {"id": "p1", "prim": {"kind": "wall", "w": 22.15, "d": 0.3, "h": 6, "material": {"map": "...", "archive": "...",
            "mesh": "...", "key": "<material uid hex>", "name": "AC3MP_FORT_..."}}, "name": "Wall 22.15 x 0.3 x 6 m",
   "solid": true, "matrix": [...]},
  {"id": "p2", "asset": "ac3mp_fort_buildingwindow_01a", "name": "AC3MP_FORT_BuildingWindow_01A_007", "matrix": [...]}]}
```

* `matrix`: the part's place relative to the asset's pivot. The pivot is the bottom middle of what was saved; in the
  asset editor it is the grid's origin. A child asset's matrix places the child's own pivot.
* `id`: stable within the asset. A part's **path** is the ids from the top down: `p2` is the window part, and
  `p2/p0` is the model inside that window asset. The project file keys a placement's parts by these paths.
* **Nesting**: an `"asset"` part references another file here by id. Editing that file changes it everywhere it is
  used, the next time an asset using it is placed. *Make asset from building* writes one child asset per model:
  every window of the barracks references the same `ac3mp_fort_buildingwindow_01a`.
* A part's map is found by its file name among the game's maps (`archive` is only the fallback), so assets keep
  working when the map files move.
* A model part may name the map object it was taken from (`"entity": "<uid hex>"`, Make asset from building and Save
  selection). Its collision and climb data are then that object's own; without it they come from the model's
  first object in the map.
* **Blend-group parts**: a static NPC group the players blend into is saved as `{"id": "p9", "blend": {"map",
  "archive", "entity": "<uid hex>"}, "matrix": [...]}` - the group as that map has it (slots, NPC specs, props), built
  by the gameplay engine (a copy with fresh ids); placed on its own map it is a copy of that group.
* Collision comes with each model as its map has it: the object's own shape (whatever its size, for a part named by
  object), each triangle on its own CollisionMaterial (FuzzyZone stays FuzzyZone). A part whose object had no
  collision of its own gets none.
* Format 1 files (no ids, no `"asset"` parts) still load: their parts get ids `p0, p1, ...` in order.
* **Ground parts** (Make asset from building): `{"id": "p137", "ground": {"map", "archive", "footprint": [[x, y],
  ...], "exclude": ["<uid hex>", ...]}, "matrix": [...]}`. This is the ground under the building's footprint in its
  map's world: its floors and plot, e.g. the barracks' upper floor, which it shares with the mess hall. It is cut from
  the map when the asset is used, so no triangles are stored here.
* `"climb"` (optional) on a model / primitive / ground part:
  * `"native"`: its own map's climb data. The default for models.
  * `"generated"`: ledges + handhold rows. The default for primitives.
  * `"none"`: no climb data. The default for ground.
  * `"outside"`: as `"native"`, plus handhold rows and ledges on the outer walls of the building it is part
    of. The building is every part of the placed asset set to it. The rows and ledges go on an invisible layer that
    the build adds; inside walls get nothing.

Assets made by the Studio are kept in `<workspace>ssets`; this folder holds the format notes.
