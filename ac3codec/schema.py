"""The class layouts the FastLoad codec reads objects with: a neutral, JSON-serialisable model and its runtime form.

The model (README.md, "The schema model") is plain data:

    {"types": {type_hash: {"name": str | None, "base": base_hash or 0, "props": [[flags, name_hash, packed_type], ...],
                           "dynamic": bool (optional), "flags": int (optional)}},
     "enums": {enum_hash: {"name": str | None, "values": {int: str}}},
     "names": {hash: str}}

`packed_type` is the property's type word << 32 | the hash of the object type it names (0 for none); `props` are the
class's OWN properties in serialization order (its base class's come first, from the base's entry). `dynamic`: the
class's serializer appends a DynamicProperties block (absent: taken from `flags`, the exe's type flags, 0x2). In JSON
the hash keys are strings ("0x1A2B3C4D"; decimal is read too); in memory they are ints.

`Schema(model)` is what the codec uses: per type its decoded properties (`Prop`), whole base chain and whether it is a
managed type. `load_json(path)` reads a model file, `from_anvilforge(schema)` converts anvilforge's loaded schema (a
dev / test adapter: the only place anvilforge is touched, never at run time in the Studio).
"""
import json
import zlib

# Property kinds: the type word's bits 16-20.
BOOL, CHAR, INT8, UINT8, INT16, UINT16, INT32, UINT32, INT64, UINT64, FLOAT = range(11)
VECTOR2, VECTOR3, VECTOR4, QUATERNION, MATRIX3X3, MATRIX4X4 = range(11, 17)
OBJECT_ID, HANDLE, OBJECT, OBJECT_PTR, BASE_OBJECT_PTR, BASE_OBJECT = range(17, 23)
STATIC_ARRAY, BIG_ARRAY, ENUM, STRING, LSTRING, REFERENCE, SMALL_ARRAY, SLOPPY_HANDLE, DATA_DRIVEN_ENUM = range(23, 32)

KIND_NAMES = ("BOOL CHAR INT8 UINT8 INT16 UINT16 INT32 UINT32 INT64 UINT64 FLOAT VECTOR2 VECTOR3 VECTOR4 QUATERNION "
              "MATRIX3X3 MATRIX4X4 OBJECT_ID HANDLE OBJECT OBJECT_PTR BASE_OBJECT_PTR BASE_OBJECT STATIC_ARRAY "
              "BIG_ARRAY ENUM STRING LSTRING REFERENCE SMALL_ARRAY SLOPPY_HANDLE DATA_DRIVEN_ENUM").split()

# Bytes per value of the fixed-size kinds (the ids' width is the codec's: 4, or 8 for AC3 singleplayer's v27 data).
FIXED_SIZE = {BOOL: 1, CHAR: 1, INT8: 1, UINT8: 1, INT16: 2, UINT16: 2, INT32: 4, UINT32: 4, INT64: 8, UINT64: 8,
              FLOAT: 4, VECTOR2: 8, VECTOR3: 12, VECTOR4: 16, QUATERNION: 16, MATRIX3X3: 36, MATRIX4X4: 64, ENUM: 4}
ARRAYS = (STATIC_ARRAY, BIG_ARRAY, SMALL_ARRAY)

SERIALIZED = 0x02000000          # property flag: written by FastLoad (the others are run-time state)
BITFIELD = 0x00400000            # type word: a bitfield member; bits 23-27 hold its width, not an element kind
MANAGED_OBJECT = zlib.crc32(b"ManagedObject")   # inline objects of types derived from it carry a flag byte
# Type flag 0x2 (the class descriptor's flags word in AC3MP.exe, carried as TypeDef flags by the .schema files): the
# class's serializer appends a DynamicProperties block after its own properties. exe_schema.py reads it from the exe;
# BuildColumn, BuildRow, FXCommand and Material carry such blocks in the archives.
TYPE_DYNAMIC = 0x2


def hash_name(name):
    """The engine's name hash: crc32 of the name."""
    return zlib.crc32(name.encode("latin-1"))


def unpack_type_word(tw):
    """-> (kind, element kind or None, static count, bitfield width or 0) of a property's type word."""
    kind = (tw >> 16) & 0x1F
    if tw & BITFIELD:
        return kind, None, tw & 0xFFFF, (tw >> 23) & 0x1F
    return kind, (tw >> 23) & 0x1F, tw & 0xFFFF, 0


class Prop:
    """One property: the model's [flags, name_hash, packed_type] with the type word unpacked."""
    __slots__ = ("flags", "name_hash", "packed_type", "name", "kind", "elem", "count", "bits", "object_hash")

    def __init__(self, flags, name_hash, packed_type, name):
        self.flags, self.name_hash, self.packed_type, self.name = flags, name_hash, packed_type, name
        self.object_hash = packed_type & 0xFFFFFFFF
        self.kind, self.elem, self.count, self.bits = unpack_type_word(packed_type >> 32)

    @property
    def type_word(self):
        return self.packed_type >> 32

    @property
    def serialized(self):
        return bool(self.flags & SERIALIZED)

    def __repr__(self):
        k = KIND_NAMES[self.kind]
        if self.kind in ARRAYS:
            k += f"<{KIND_NAMES[self.elem]}>" + (f"[{self.count}]" if self.kind == STATIC_ARRAY else "")
        return f"Prop({self.name} {k} flags={self.flags:#x} obj={self.object_hash:#010x})"


class TypeInfo:
    """One class: its own properties, base and the derived facts the codec needs."""
    __slots__ = ("type_hash", "name", "base", "props", "dynamic", "managed", "chain")

    def __init__(self, type_hash, name, base, props, dynamic):
        self.type_hash, self.name, self.base, self.props, self.dynamic = type_hash, name, base, props, dynamic
        self.managed = False
        self.chain = None

    def __repr__(self):
        return f"TypeInfo({self.name or hex(self.type_hash)} base={self.base:#010x} props={len(self.props)})"


class Schema:
    """The runtime form of a model: `types_by_hash`, `name_of`, `type_by_hash`, `chain`."""

    def __init__(self, model):
        self.model = model
        self.names = {int(h): s for h, s in model.get("names", {}).items()}
        self.types_by_hash = {}
        self.enums_by_hash = {}
        for h, e in model.get("enums", {}).items():
            h = int(h)
            if e.get("name"):
                self.names.setdefault(h, e["name"])
            self.enums_by_hash[h] = {int(v): s for v, s in e.get("values", {}).items()}
        for h, t in model["types"].items():
            h = int(h)
            if t.get("name"):
                self.names.setdefault(h, t["name"])
        for h, t in model["types"].items():
            h = int(h)
            props = [Prop(f, n, pt, self.name_of(n)) for f, n, pt in t.get("props", ())]
            dyn = t.get("dynamic", bool(t.get("flags", 0) & TYPE_DYNAMIC))
            self.types_by_hash[h] = TypeInfo(h, t.get("name"), t.get("base") or 0, props, bool(dyn))
        for t in self.types_by_hash.values():
            t.chain = self._chain(t)
            t.managed = any(c.type_hash == MANAGED_OBJECT for c in t.chain)

    def _chain(self, t):
        out, seen = [], set()
        while t is not None and t.type_hash not in seen:
            seen.add(t.type_hash)
            out.append(t)
            t = self.types_by_hash.get(t.base) if t.base else None
        return out[::-1]

    def name_of(self, h):
        """The name of a hash, or 0x%08X when it has none."""
        s = self.names.get(h)
        return s if s is not None else f"0x{h:08X}"

    def type_by_hash(self, h):
        return self.types_by_hash.get(h)

    def chain(self, h):
        """-> the class and its bases, base first ([] for an unknown class)."""
        t = self.types_by_hash.get(h)
        return t.chain if t is not None else []

    def is_managed(self, h):
        t = self.types_by_hash.get(h)
        return t is not None and t.managed

    def enum_value_name(self, enum_hash, value):
        return self.enums_by_hash.get(enum_hash, {}).get(value)

    def to_json(self, path):
        save_json(self.model, path)


def _key(h):
    return f"0x{h:08X}"


def model_to_json(model):
    """-> the model with hex-string keys (JSON object keys are strings)."""
    return {
        "types": {_key(int(h)): t for h, t in sorted(model["types"].items(), key=lambda kv: int(kv[0]))},
        "enums": {_key(int(h)): {"name": e.get("name"), "values": {str(int(v)): s for v, s in e["values"].items()}}
                  for h, e in sorted(model.get("enums", {}).items(), key=lambda kv: int(kv[0]))},
        "names": {_key(int(h)): s for h, s in sorted(model.get("names", {}).items(), key=lambda kv: int(kv[0]))},
    }


def model_from_json(data):
    """-> the model with int keys, from its JSON form (keys "0x1A2B3C4D" or decimal)."""
    return {
        "types": {int(h, 0): {"name": t.get("name"), "base": t.get("base") or 0,
                              "props": [list(p) for p in t.get("props", ())],
                              **({"flags": t["flags"]} if "flags" in t else {}),
                              **({"dynamic": True} if t.get("dynamic") else {})}
                  for h, t in data["types"].items()},
        "enums": {int(h, 0): {"name": e.get("name"), "values": {int(v, 0): s for v, s in e.get("values", {}).items()}}
                  for h, e in data.get("enums", {}).items()},
        "names": {int(h, 0): s for h, s in data.get("names", {}).items()},
    }


def save_json(model, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(model_to_json(model), fh, indent=0, separators=(",", ":"))


def load_json(path):
    """-> Schema from a model file."""
    with open(path, encoding="utf-8") as fh:
        return Schema(model_from_json(json.load(fh)))


def from_anvilforge(anvil_schema):
    """-> the neutral model of anvilforge's loaded schema (anvilforge.schema.Schema): a dev / test adapter.

    Takes only its data (types, enums, names); the class-local enums join the global ones; `flags` are the type
    flags, and `dynamic` is set from TYPE_DYNAMIC."""
    names = {int(h): s for h, s in anvil_schema.names.items()}
    types, enums = {}, {}

    def add_enum(e):
        enums[e.name_hash] = {"name": names.get(e.name_hash),
                              "values": {v.value: names.get(v.name_hash, f"0x{v.name_hash:08X}") for v in e.values}}

    for e in getattr(anvil_schema, "global_enums_by_hash", {}).values():
        add_enum(e)
    for h, t in anvil_schema.types_by_hash.items():
        types[h] = {"name": names.get(h), "base": t.base_type_hash or 0, "flags": t.flags,
                    "props": [[p.flags, p.name_hash, p.packed_type] for p in t.properties]}
        if t.flags & TYPE_DYNAMIC:
            types[h]["dynamic"] = True
        for e in t.enums:
            add_enum(e)
    return {"types": types, "enums": enums, "names": names}
