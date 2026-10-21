# ac3codec: Reforged Studio's FastLoad object codec

`ac3codec` reads and writes the bodies of Assassin's Creed III's game objects (the FastLoad format of a forge
archive's objects) against a neutral schema model. It replaces anvilforge-py's `anvilforge.fastload` and
`anvilforge.schema` in the Studio, with the same interface: our own implementation of the format this file
describes. Licence: GPL v3 or later, as Reforged Studio.

Format knowledge from anvilforge-py by Patrick Munnich / ACBMP, with his permission. anvilforge is the reference the
gate compares against (`tests/gate.py`), and `schema.from_anvilforge` reads its schema data. "How each rule was
established" below records where each part of the format came from.

```python
from ac3codec import Codec, load_json
C = Codec(load_json("AC3_MP.schema.json"))
root = C.decode(body)            # Root(pre_header, status, obj)
root.obj.fields["Density"]       # b'\n\xd7\xa3<' - every fixed-size value is its bytes
assert C.encode(root) == body
```

## The API

| name | what |
|---|---|
| `Codec(schema, id_size=4)` | `.decode(body) -> Root`, `.encode(root) -> bytes`. `id_size=8` reads AC3 singleplayer's v27 data, where every object id is 8 bytes (the root's and every inline object's id, a pointer's link, a reference's id, a handle's id, an `OBJECT_ID`). |
| `Root(pre_header, status, obj)` | one object body. `pre_header` is b"" except for the bodies that start with `01` (below). |
| `Obj(type_hash, id, fields={}, flag=None, dyn=None)` | an object. `fields` is an ordered dict keyed by property name, in serialization order. `flag` is the flag byte, or None when the object has none. `dyn` is a DynamicProperties block, `[(name_hash, object_hash, type_word, value)]`, or None when the class has none. |
| `Ptr(status, link=None, obj=None)` | an object pointer. Status 3 = null; 1, 2 and 5 carry `link` (an id); 0 and 4 carry `obj` (inline). |
| `Ref(tag, extra, id, obj=None)` | a reference. Tag 0 carries `obj` (inline, with the reference's id); other tags carry only the id. |
| `Handle(tag, id)` | a handle. |
| `DecodeError` | bytes that don't follow the schema. Has `.offset` and `.path` (the property path). |
| `EncodeError` (a `ValueError`) | a tree the schema can't write: a missing field, a value of the wrong size, or a value of the wrong shape. |
| `Schema(model)` | the runtime schema: `types_by_hash` (dict), `type_by_hash(h)`, `name_of(h)` (the name, or `0x%08X`), `chain(h)` (the class and its bases, base first), `is_managed(h)`, `enum_value_name(enum, value)`; `model` is the model it was built from. |
| `load_json(path)` / `save_json(model, path)` | a model file: read it into a Schema / write a model to it. |
| `from_anvilforge(anvil_schema)` | the model of anvilforge's loaded schema. A dev / test adapter: the only code that touches anvilforge, and never used at run time in the Studio. |

Value shapes, as the Studio's modules use them:

* bytes for every fixed-size value: bools, ints, floats, vectors, matrices, enums, `OBJECT_ID`s, and bitfield members at their kind's full size;
* bytes for a string, without its NUL (an LSTRING's UTF-16 bytes);
* a list for an array: a byte array is a list of 1-byte bytes, a float array a list of 4-byte bytes, and so on;
* `Obj` / `Ptr` / `Ref` / `Handle` for the rest.

A Codec caches one read / write plan per class. Build one Codec per schema and reuse it.

## The schema model

The model is plain, JSON-serialisable data. `exe_schema.py` produces the same model from the user's AC3MP.exe
(EXE_SCHEMA.md).

```
{"types": {type_hash: {"name": str | None,
                       "base": base_hash or 0,
                       "props": [[flags, name_hash, packed_type], ...],
                       "dynamic": true,                    # optional, see below
                       "flags": int}},                     # optional: the exe's type flags
 "enums": {enum_hash: {"name": str | None, "values": {int: str}}},
 "names": {hash: str}}
```

* `props` are the class's own properties, in serialization order, all of them. The codec keeps only those whose
  flags have `SERIALIZED` (0x02000000); the rest are run-time state. A class's base's properties come from the base's
  own entry.
* `packed_type` is `type word << 32 | object hash`, the same pair the exe's 32-byte property records carry. The
  object hash is the type an object, pointer or enum property names (0 for none). The type word packs:

  | bits | field |
  |---|---|
  | 16-20 | the kind (`schema.KIND_NAMES`: BOOL 0 ... DATA_DRIVEN_ENUM 31) |
  | 22 | set for a bitfield member |
  | 23-27 | the element kind of an array; for a bitfield member, its width |
  | 0-15 | the element count of a static array |

* `dynamic: true` is the one field the codec needs beyond types, properties and enums: the class's serializer
  appends a DynamicProperties block after the class's own properties. It is type flag 0x2 of the class descriptor
  in AC3MP.exe (`schema.TYPE_DYNAMIC`, read by `exe_schema.py`). When `dynamic` is absent, the codec takes it from
  `flags`. In AC3 MP that is 16 classes: BuildColumn, BuildRow, CoreFXManager, DynamicPropertiesSet, FXCommand,
  FXConstantTable, FXConstantTableInstance, FXDefaultTable, FXDefaultTableInstance, FXProperty, GenericObject,
  Material, PropertyControllerEntry, SaveGame, SaveGameDataObject and SaveGameObject. BuildColumn, BuildRow,
  FXCommand, Material and CoreFXManager are seen carrying blocks in the archives.
* `flags` (optional) are the exe's type flags, kept for tools.
* Enums are information for tools: the codec writes an enum as 4 raw bytes.
* Names are crc32 of the name (`schema.hash_name`).
* In JSON the hash keys are strings, `"0x1A2B3C4D"` (decimal is read too), and the enum value keys are decimal
  strings. In memory every key is an int.

The codec derives the rest itself:

* **managed**: the class derives from ManagedObject (crc32 0xBB96607D);
* **kind, element, count and bit width**: from the type word.

## The format

All values are little-endian. `id` is 4 bytes (8 with `id_size=8`).

```
body          [pre-header] status u8, flag u8, id, type u32, the type's properties
pre-header    only when body[0] == 1 (Animation, NavMeshManager, ProxyTextures): u8 1, u8, u16, i32 n, n x 12 bytes.
              It is kept verbatim. Root.status is the byte at 12 n + 7 (the table's last), and the flag follows it.
properties    for each class of the chain, base first: its SERIALIZED properties in order, then a DynamicProperties
              block if the class is `dynamic`

BOOL CHAR INT8..UINT64 FLOAT VECTOR2..4 QUATERNION MATRIX3X3/4X4 ENUM
              1 1 1..8 4 8/12/16 16 36/64 4 bytes - a bitfield member is written at its kind's full size
OBJECT_ID     id
HANDLE        u8 tag, id
STRING        u32 n, then n bytes and a NUL; n = 0 has no NUL
LSTRING       u32 n, then 2n bytes (UTF-16) and a 2-byte NUL; n = 0 has no NUL
OBJECT        [u8 flag], id, u32 type, that type's properties
BASE_OBJECT   the same. The flag byte is there only when the property's declared type (its object hash) is managed.
OBJECT_PTR    u8 status: 3 null; 1, 2, 5: id (a link); 0, 4: an inline OBJECT (flag rule as above)
BASE_OBJECT_PTR  the same
REFERENCE     u8 tag, u8 extra, id; tag 0: + u32 type, that type's properties (the object's id = the reference's)
STATIC_ARRAY  the type word's count of elements
BIG_ARRAY / SMALL_ARRAY  u32 count, the elements
DynamicProperties  u32 n, n x {u32 name hash, u32 object hash, u32 type word, the value as that type word and object
              hash say (the same rules, the flag byte included)}
```

## What it covers

`tests/gate.py` checks every top-level object of DataPC.forge, DataPC_extra.forge and every other DataPC_*.forge of
the multi folder. It requires a byte-identical round trip, and the same tree as anvilforge wherever anvilforge
decodes the object. The recorded run (`tests/gate_report.txt`, on an install that includes modded map archives)
covered 43 archives and 577,819 objects:

* **PASS.** 422,113 objects decode to the same tree as anvilforge's and are written back byte for byte.
* 1 more object decodes with ours only, and is also written back byte for byte: CoreFXManager, a dynamic class
  by the exe's flag.
* No failures, and no object that anvilforge decodes and ours doesn't.
* 155,705 objects neither codec decodes:
  * 135,577 Animations, whose track data has a hand-written serializer;
  * FX code tables, MaterialTemplate shader permutations, NavMeshManager triangles and PropertyControllerData,
    also hand-written;
  * objects in the ported maps whose classes come from another game's layout (unknown class hashes, or more bytes
    than AC3's layout).

  Decoding them would need a reader for each, or the source game's schema.

The same gate with `exe_schema.py`'s model read from AC3MP.exe (`tests/gate_report_exe_model.txt`, fields compared
by position, since the exe holds few property names) gives the same counts.

`tests/sp64.py` checks `id_size=8` the same way on samples of the singleplayer archives, against forgelib's
`spmaps/codec27.py` (`tests/sp64_report.txt`: 31 archives, 109,373 objects). 18,500 objects match codec27 and
round-trip; the rest neither reads, since AC3_MP's layouts differ from SP's. `tests/test_codec.py` covers every
rule on a small hand-made schema, with no game files.

```
python ac3codec/tests/test_codec.py             # the rules, no game files
python ac3codec/tests/gate.py                   # every archive, 6 workers, compared with anvilforge
python ac3codec/tests/gate.py --schema m.json   # a model file (e.g. the exe's); fields compared by position
python ac3codec/tests/gate.py --find 0x053FF163 # also count a class wherever it occurs, top level or nested
python ac3codec/tests/gate.py --dump-model m.json --archives DataPC_Fire.forge   # write anvilforge's model as JSON
python ac3codec/tests/sp64.py                   # the 64-bit-id mode
```

## How each rule was established

| rule | source |
|---|---|
| the archive layer: records, the object directory, object headers (`u32 type, u32 size, u32 name length, name`) | forgelib's `forge.py`, `datafile.py`, `objects.py`, `maps/mapwalk.py` (our own readers) |
| root header: status, flag, id, type hash | `objects.py`'s notes (a uid-bearing body starts `00 01 uid type`), then the bytes |
| the pre-header of bodies starting `01` (12 n + 7 bytes) | `objects.py`'s notes (an Animation's clip id at 9 + 12 n) and `spmaps/codec27.py` |
| 8-byte ids in singleplayer v27 data | `spmaps/codec27.py`'s docstring |
| `packed_type = type word << 32 \| object hash` | `spmaps/exe_schema.py`'s docstring (the exe's 32-byte property records) |
| the type word's bit fields (kind, bitfield bit, element / width, static count) | each schema type word compared with the kind, element and count anvilforge reports for it (all 3,968 classes agree) |
| kind numbers, labels and sizes | anvilforge's `Kind` names and sizes; every size confirmed by the byte-identical gate |
| properties are written base class first, only those with flag 0x02000000 | anvilforge's decoded field lists compared with the schema: every property with the flag present, none without, in chain order |
| an inline object's flag byte exists only for a ManagedObject-derived declared type | anvilforge's trees: whether `Obj.flag` is set, per declared type, against each type's base chain; also seen in Material's DynamicProperties (TextureSelector has the byte, UVTransform not) |
| pointer status (3 null; 1/2/5 a link; 0/4 inline), reference (tag, extra, id; tag 0 inline), handle (tag, id) | the bytes (TextureSet, EntityBuilder, World) and anvilforge's trees; the handle's tag byte as in anvilforge commit 2f07b9a |
| STRING (n, bytes, NUL; none when empty), LSTRING (n UTF-16 characters + a 2-byte NUL) | the bytes: CrowdDutyRegion.RegionName, UIString.TempString in "Game Bootstrap Settings" (non-empty TempStrings show the LSTRING rule) |
| the DynamicProperties layout (count; name, object hash, type word, value) | the bytes of EntityBuilder's BuildColumn / BuildRow and of Material, after `Obj.dyn` in anvilforge's output |
| which classes carry DynamicProperties | the exe's type flag 0x2 (16 classes: anvilforge's list of 12 + CoreFXManager and three SaveGame classes), read by `exe_schema.py`; CoreFXManager's object decodes with it |

Provenance: the codec is not derived from anvilforge's source. anvilforge served as a black-box reference (its
decoded trees and re-encoded bytes compared with ours), with its public module attributes listed by introspection
(the dataclass fields, the `Kind` enum, its size table, its list of DynamicProperties classes, its method names);
`spmaps/codec27.py`, which subclasses anvilforge's codec, was read in full. The codec's structure is its own:
per-class read / write plans of closures, built once per Codec. The one anvilforge source change consulted, with
Patrick Munnich's permission, is a1bab18 (the TeamVIPNavflowPathNode schema fix); `from_anvilforge` carries it over,
because it reads anvilforge's loaded schema.
