"""forgelib's `codec27` interface (AC3 singleplayer's FastLoad bodies, 8-byte ids) on the Studio's own codec.

forgelib's spmaps readers (sp_assets, sp_topview, exe_schema) import `codec27`, which wraps anvilforge's codec. In a
Studio process mapdata registers this module as `codec27` first, so those readers decode with ac3codec
(`Codec(schema, id_size=8)`, which decodes the same as codec27) and the schema read from the game's own exes - no
anvilforge. What they use of it:

* `mp_schema()` - the MP schema (mapdata.schema(), from AC3MP.exe) in the shape spmaps/exe_schema reads: `types_by_hash`
  values with `type_hash`, `base_type_hash`, `properties` ([Property]); `type_by_hash`, `name_of`.
* `sp_schema()` - mp_schema() with every class AC3SP.exe lays out differently replaced by its SP table
  (spmaps/exe_schema.sp_tables(), cached by forgelib's reader); `geometry_schema()` - with MP_PREFIX on top.
* `Codec64(schema)` / `PartialCodec64(schema)` (`.made`: every object started, in order, each with the fields read
  before a failure), `DecodeError`, `Property(flags, name_hash, packed_type)`.
"""
import zlib

from ac3codec import Codec, DecodeError, Handle, Obj, Ptr, Ref, Root, Schema  # noqa: F401
from ac3codec import schema as K

ID = 8


class Property:
    """A property as the schema model holds it: [flags, name hash, type word << 32 | object hash]."""
    __slots__ = ("flags", "name_hash", "packed_type")

    def __init__(self, flags, name_hash, packed_type):
        self.flags, self.name_hash, self.packed_type = flags, name_hash, packed_type

    @property
    def object_hash(self):
        return self.packed_type & 0xFFFFFFFF

    def __repr__(self):
        return f"Property({self.flags:#x}, {self.name_hash:#010x}, {self.packed_type:#x})"


class _Type:
    __slots__ = ("type_hash", "base_type_hash", "name", "properties", "extra")

    def __init__(self, h, t):
        self.type_hash, self.base_type_hash, self.name = h, t.get("base") or 0, t.get("name")
        self.properties = [Property(*p) for p in t.get("props", ())]
        self.extra = {k: v for k, v in t.items() if k in ("flags", "dynamic")}


class SchemaView:
    """An editable view of a schema model (spmaps' readers replace classes' `properties`); `.schema()` -> the
    ac3codec Schema of the edited model."""

    def __init__(self, model):
        self.names = dict(model.get("names", {}))
        self.enums = model.get("enums", {})
        self.types_by_hash = {int(h): _Type(int(h), t) for h, t in model["types"].items()}
        self._names = Schema({"types": {}, "names": self.names})

    def name_of(self, h):
        return self._names.name_of(h)

    def type_by_hash(self, h):
        return self.types_by_hash.get(h)

    def model(self):
        return {"types": {h: {"name": t.name, "base": t.base_type_hash, **t.extra,
                              "props": [[p.flags, p.name_hash, p.packed_type] for p in t.properties]}
                          for h, t in self.types_by_hash.items()},
                "enums": self.enums, "names": self.names}

    def schema(self):
        return Schema(self.model())


def _codec_schema(S):
    return S.schema() if isinstance(S, SchemaView) else S


class Codec64(Codec):
    """The codec with 8-byte object ids (AC3 singleplayer's v27 data), on a SchemaView or an ac3codec Schema."""

    def __init__(self, schema):
        super().__init__(_codec_schema(schema), id_size=ID)


class PartialCodec64(Codec64):
    """Codec64 that keeps what it decoded before a failure: `self.made` lists every Obj it started, in order, each
    with the fields read so far (SP's Mesh fails late, in hand-serialized CompiledMesh.InstancingData; its MeshData -
    the buffers - is whole by then)."""

    def decode(self, data):
        self.made = []
        return super().decode(data)

    def _read_props(self, b, p, obj):
        if obj.type_hash in self._plans or self.S.type_by_hash(obj.type_hash) is not None:
            self.made.append(obj)              # (an unknown class fails before it is started, as in codec27)
        return super()._read_props(b, p, obj)


def mp_schema():
    from . import mapdata
    return SchemaView(mapdata.schema().model)


def sp_schema():
    """mp_schema() with every class AC3SP.exe lays out differently replaced by its SP property table in serialization
    order (forgelib's spmaps/exe_schema.py)."""
    import exe_schema
    S = mp_schema()
    for h, props in exe_schema.sp_tables().items():
        if h in S.types_by_hash:
            S.types_by_hash[h].properties = [Property(p.flags, p.name_hash, p.packed_type) for p in props]
    return S


# Classes AC3SP.exe's reflection table gets wrong (hand-written serializers): MeshData = MP's first 9 properties,
# MeshInstancingData = all of MP's, in MP's order (the same MP_PREFIX as forgelib's spmaps/codec27.py).
MP_PREFIX = {"MeshData": 9, "MeshInstancingData": None}


def geometry_schema():
    """sp_schema() with MP_PREFIX applied: what spmaps reads meshes with."""
    S, M = sp_schema(), mp_schema()
    for nm, n in MP_PREFIX.items():
        h = zlib.crc32(nm.encode())
        S.types_by_hash[h].properties = list(M.types_by_hash[h].properties)[:n]
    return S


SERIALIZED = K.SERIALIZED
