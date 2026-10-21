# The schema from the user's AC3MP.exe (`exe_schema.py`)

`ac3codec/exe_schema.py` reads the object schema the FastLoad codec needs straight out of the user's own
`AC3MP.exe`. The Studio ships no schema file and no third-party data.

    python -m ac3codec.exe_schema [AC3MP.exe] [-o model.json] [--names-from DIR ...]
    python ac3codec/tests/test_exe_schema.py          (against anvilforge's AC3_MP.schema, the reference)

```python
from ac3codec import exe_schema
model = exe_schema.read_exe(path_to_exe, extra_names=exe_schema.source_names("ac3editor", "studio"))
```

`read_exe` takes about 2 s. It returns the neutral model (`ac3codec/schema.py`): `types` {hash: name, base, props},
`enums` {hash: name, values {int: str}}; a type also has `flags` (its type flags) and `dynamic: True` when they have 0x2, `names` {hash: str}. `to_json(model)` gives the codec's JSON form (hex
string keys).

## Where each piece of the model comes from

Everything in the model is read from the exe. Nothing is taken from AC3_MP.schema; the test only compares
against it.

| model | source in the exe |
|---|---|
| the list of types | the class descriptors in `.data`, found by their 0x7FFF7FFF marker (+40) with a name string at +8 |
| `types[h]["name"]` | the descriptor's name string (+8) |
| `types[h]["base"]` | descriptor +12 |
| `types[h]["props"]` | the descriptor's property table (+0, count at +76 bits 0-15): each 32-byte record's flags, name hash and type word << 32 \| object hash, verbatim |
| property order | the order of each record's static pointer in `.data` |
| `types[h]["flags"]` | descriptor +24 (the type flags) |
| `types[h]["dynamic"]` | descriptor +24, type flag 0x2 (the codec also takes it from `flags` when `dynamic` is absent) |
| class-local enums | the descriptor's enum table (+4, count at +76 bits 16-23): 10-byte entries, then {value, name hash} pairs |
| other ("global") enums | the same 10-byte entry, found in `.data` from the hash an enum-kind property names |
| `names` | type names from the descriptors; every other name = an exe string (ASCII / UTF-16), an identifier in one, or `Class::Identifier`, kept only when its crc32 is a hash the model uses; plus the caller's `extra_names` under the same check |

The only constants in the code are layout facts: the descriptor and record sizes, the marker, and the bitfield
bit (type word bit 22), whose element kind field means a width instead.

## Credits

anvilforge (anvilforge-py, by Patrick Munnich) is the reference this was checked against, used with his
permission. `tests/test_exe_schema.py` loads its AC3_MP.schema through anvilforge's own loader and compares that
with the exe's model. Its schema format also gave the target shape: properties as (flags, name hash, packed type),
and global plus class-local enums. The Studio ships nothing of it.

## How AC3MP.exe describes its classes

Measured on AC3MP.exe (the game's 2012-10-18 build, 21,595,216 bytes, sha1 `e1dbde34…`). Offsets are bytes, words u32.

**Class descriptor** (80 bytes, in `.data`; there is one per reflected class, 3,968 in all):

| at | what |
|---|---|
| +0 | property table (VA; 0 when the class has no properties) |
| +4 | enum table (VA; 0 when none) |
| +8 | name (VA of an ASCII string) |
| +12, +16 | base type hash, type hash |
| +20 | instance size |
| +24 | type flags (= AC3_MP.schema's TypeDef flags for all 3,968 classes). 0x2: the serializer appends a DynamicProperties block. It is set on anvilforge's 12 DynamicProperties classes, plus CoreFXManager, SaveGame, SaveGameDataObject and SaveGameObject. The model's `dynamic` comes from this flag. |
| +40 | 0x7FFF7FFF (the marker the scan finds descriptors by: marker, a name string, a non-zero hash) |
| +44 … +72 | functions (constructor, …) |
| +76 | bits 0-15 the property count, bits 16-23 the class-local enum count, bits 24-31 a kind (0x04 / 0x08 / 0x10 / …, not needed) |

**Property record** (32 bytes, `count` of them from +0):
`u32 flags, u32 name hash, u32 object hash, u32 type word, u32 field position, u32 getter, u32 setter, u32 0`.
This gives the model's `[flags, name_hash, type_word << 32 | object_hash]` one to one. In the position word,
bits 18+ are the byte offset of the field and bits 5-15 its bit offset for a bitfield member. Properties with
no storage have a getter and setter instead.

**Serialization order.** The table is in layout order: a class's bitfield members come first (Bone: `Index`,
`ChildBoneCount` first). FastLoad uses the declaration order. Every property record also has a static pointer to
it in `.data` (the `push dword ptr [global]` operands the generated FastLoad readers use), and those pointers lie
in declaration order, with the class's descriptor pointer next to them. So `exe_schema` sorts each class's records
by the address of the `.data` word that points to it, leaving out the descriptor's own +0. Two classes
(`InvestigationIconUIEvent`, `UnlockableTextImages`) have a record that other data points to as well. For those
the pointer nearest the class's other pointers counts. Every record of every class has a pointer, so the table
order is never the fallback.

**Enums.** An enum table is `n` packed 10-byte entries `{u32 values (VA), u32 enum hash, u16 count}`, and the
values are `count` pairs `{i32 value, u32 name hash}`. The class-local enums come from the descriptors (684).
The enums no class lists (the "global" ones) come from the hash that an enum-kind property (or an array of
them) names, searched in `.data` for the same 10-byte entry (281, each found exactly once). The enum kind is not
hard-coded. It is the kind of the properties that name the classes' own enums: type word bits 16-20 = 25 on
AC3MP.exe, and the same as an array's element kind (bits 23-27). The model has 965 enums: every
enum in AC3_MP.schema plus `0x84A820FD`, which `AC2PadButtonReader` names and the schema lacks.

**Names.** A type's name is its descriptor's string: all 3,968, including the 119 whose hash is not crc32 of
the name (old ids such as `AIBootstrap` 0x6F55EC09; `FightManager`'s hash is crc32("FightManagerv2"), so a
string search would name it wrongly, but the descriptor does not). Every other name is crc32 of its text. The
retail exe keeps only some of those strings: `names` holds every hash the model uses whose text is an exe
string (ASCII or UTF-16), an identifier inside one, a class-local enum's `Class::Identifier`, or one of the
caller's `extra_names`. A candidate counts only when its crc32 is a hash the exe's model actually uses, so
`extra_names` cannot add names the game doesn't have.

| | in AC3_MP.schema | named from the exe alone | + the Studio's source (`source_names("ac3editor", "studio", "tools")`) |
|---|---|---|---|
| types | 3,968 | 3,968 | 3,968 |
| property names | 11,306 | 812 | 1,132 |
| enum names | 962 | 60 | |
| enum value names | 7,164 | 222 | |
| all names | 22,802 | 4,568 (4,562 shared, none different) | 4,952, none different |

**Names don't matter for decoding.** The codec reads properties by their hashes and kinds. A name is a display
string and the key of `Obj.fields`, and an unnamed hash is keyed `0x%08X`. Any name the Studio's code reads a
field by is in its own source, so `source_names` over that source names it. The exception would be a name the
code builds at run time (an f-string): add it to `extra_names`.

## Gated on the archives

The codec's full gate (`ac3codec/tests/gate.py --schema`) with the exe model as the codec's schema (report
`ac3codec/tests/gate_report_exe_model.txt`) covers 43 multi archives and 577,819 objects:

- 422,113 decode to the same tree as anvilforge's (fields compared by position), with a byte-identical round trip;
- 1 more decodes only with this model (CoreFXManager, a DynamicProperties class by type flag 0x2);
- there are 0 failures.

That is the same result as the gate's run on anvilforge's own schema.

## Differences from AC3_MP.schema

`test_exe_schema.py` compares every type, base, flags, name, property list (flags, hashes, type words, order), enum and
name the reference schema holds. Everything is identical except the following. They are differences of game
build: the schema was dumped from another build of AC3.

| what | AC3_MP.schema | AC3MP.exe (all three below) | for decoding |
|---|---|---|---|
| `TargetCoordinatorOutEvent` | Type, Index, IsBoss (BOOL, 1 byte) | Type, Index | The exe's class has no IsBoss, so this build reads 2 properties. It is an Event (a run-time message). |
| `ReactionCondition` | ReactionState first | ReactionState last | The exe is right for this build: its FastLoad reader (0x9BB7E5) reads ResolveType, AnyReaction, ReactionLevel, LevelConditionType, Reactions, then ReactionState (field +0x34). Both orders consume the same bytes (two 4-byte enums), so a round trip agrees either way. Only the exe order gives the right values. |
| enum `Pad::PadButton` | 24 values (an extra 21, the rest renumbered) | 23 values | Display only (enum values decode as 4 bytes). |
| enum `GameModeEvent::GameModeEventType` | 16 values | 15 values (no 15) | Display only. |
| enum `0x84A820FD` | absent | 18 values | Display only. |

Neither `TargetCoordinatorOutEvent` nor `ReactionCondition` occurs in any decoded object, top level or nested, of
the 43 multi archives the gate covers (`gate.py --find`). So on this data the two type differences can't change a
decode.

forgelib's `spmaps/exe_schema.py` (the singleplayer schema reader) treats descriptor +4 as a "declaration list"
(it is the enum table) and places bitfield blocks using AC3_MP.schema's own order, so it matches 3,923 classes,
not all of them. This module needs neither.

## Patched and unpatched exes

The Ubisoft Connect exe (`…\Ubisoft Game Launcher\games\Assassin's Creed III\AC3MP.exe`) and the retail install's
(`…\Ubisoft\Assassin's Creed III\AC3MP.exe`) are the same file (same sha1). A patched AC3MP.exe (21,635,072 bytes,
sha1 `ef67b711…`) gives an identical model: types, enums and names. Its patches change code and add data, not the
reflection tables. To compare exes: `set AC3_EXES=<exe1>;<exe2>` and run the test.
