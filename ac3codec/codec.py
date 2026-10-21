"""AC3's FastLoad object format: decode an object's body into a tree, encode a tree back to the same bytes.

    root = Codec(schema).decode(body)      # Root(pre_header, status, obj)
    body = Codec(schema).encode(root)

The rules (README.md, "The format"), all little-endian:

    body         [pre-header] status u8, flag u8, id, type u32, the type's properties
    pre-header   only when body[0] == 1 (Animation, NavMeshManager, ProxyTextures): u8 1, u8, u16, i32 n, n x 12 bytes;
                 kept verbatim, and as in the decoded trees the Studio already handles, `status` is the byte at
                 12 n + 7 (the last of the table), the flag follows it
    properties   for each class of the chain, base first: its properties whose flags have SERIALIZED, in order;
                 then, for a class whose serializer adds one, a DynamicProperties block
    id           4 bytes (8 in AC3 singleplayer's v27 data: Codec(schema, id_size=8))

    fixed kinds  BOOL / CHAR / ints / FLOAT / vectors / matrices / ENUM: their bytes (a bitfield member too)
    OBJECT_ID    id
    HANDLE       u8 tag, id                                  -> Handle(tag, id)
    STRING       u32 n, n bytes, u8 0 (none when n is 0)      -> the n bytes
    LSTRING      u32 n, 2n bytes (UTF-16), u16 0 (none when n is 0) -> the 2n bytes
    OBJECT       [u8 flag], id, u32 type, its properties      -> Obj; the flag only for a managed declared type
    OBJECT_PTR   u8 status: 3 null; 1 / 2 / 5 + id (a link); 0 / 4 + an inline OBJECT    -> Ptr(status, link, obj)
    REFERENCE    u8 tag, u8 extra, id; tag 0: + u32 type, its properties (the object's id is the reference's)
                                                             -> Ref(tag, extra, id, obj)
    arrays       STATIC: the schema's count; BIG / SMALL: u32 count; then the elements      -> list
    Dynamic      u32 n, n x {u32 name hash, u32 object hash, u32 type word, the value as that type word says,
                 the same rules as a property} -> Obj.dyn = [(name, object hash, type word, value)]

Values come out as the Studio's modules use them: bytes for every fixed-size value (a byte array is a list of
1-byte bytes), Obj / Ptr / Ref / Handle for the rest, `Obj.fields` an ordered dict by property name.
"""
import struct
from dataclasses import dataclass, field

from . import schema as K

_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_BYTE = [bytes((i,)) for i in range(256)]


class DecodeError(Exception):
    """The bytes don't follow the schema: `offset` where reading failed, `path` the property path to it."""

    def __init__(self, message, offset=None, path=None):
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.path = list(path) if path else []

    def __str__(self):
        where = f" at {self.offset}" if self.offset is not None else ""
        path = f" ({'.'.join(self.path)})" if self.path else ""
        return f"{self.message}{where}{path}"


class EncodeError(ValueError):
    """A tree the schema can't write (a missing field, a value of the wrong size or shape)."""

    def __init__(self, message, path=None):
        super().__init__(message)
        self.message = message
        self.path = list(path) if path else []

    def __str__(self):
        return f"{self.message} ({'.'.join(self.path)})" if self.path else self.message


@dataclass
class Obj:
    type_hash: int
    id: bytes
    fields: dict = field(default_factory=dict)
    flag: int | None = None
    dyn: list | None = None


@dataclass
class Ptr:
    status: int
    link: bytes | None = None
    obj: Obj | None = None


@dataclass
class Ref:
    tag: int
    extra: int
    id: bytes
    obj: Obj | None = None


@dataclass
class Handle:
    tag: int
    id: bytes


@dataclass
class Root:
    pre_header: bytes
    status: int
    obj: Obj


PTR_LINK = (1, 2, 5)
PTR_INLINE = (0, 4)
PTR_NULL = 3


def _short(p, want, have):
    return DecodeError(f"needs {want} bytes, {max(have - p, 0)} left", p)


class Codec:
    """Decode / encode FastLoad bodies against a Schema (ac3codec.schema)."""

    def __init__(self, schema, id_size=4):
        self.S = schema
        self.id_size = id_size
        self._plans = {}          # type hash -> [(name, reader, writer) | None (a DynamicProperties block)]
        self._value_cache = {}    # (kind, elem, count, object hash) -> (reader, writer)

    # -- plans -------------------------------------------------------------------------------------------------

    def plan(self, type_hash):
        """-> the steps that read / write one object of this class: [(name, reader, writer)], None = dyn block."""
        pl = self._plans.get(type_hash)
        if pl is None:
            t = self.S.type_by_hash(type_hash)
            if t is None:
                raise DecodeError(f"unknown type 0x{type_hash:08X}")
            pl = []
            for c in t.chain:
                for p in c.props:
                    if p.flags & K.SERIALIZED:
                        r, w = self.value_io(p.kind, p.elem, p.count, p.object_hash)
                        pl.append((p.name, r, w))
                if c.dynamic:
                    pl.append(None)
            self._plans[type_hash] = pl
        return pl

    def value_io(self, kind, elem, count, oh):
        """-> (reader, writer) of one value: reader(b, p) -> (value, p), writer(out, value)."""
        key = (kind, elem, count, oh)
        io = self._value_cache.get(key)
        if io is None:
            io = self._value_cache[key] = self._make_io(kind, elem, count, oh)
        return io

    def _make_io(self, kind, elem, count, oh):
        idw = self.id_size
        if kind in K.FIXED_SIZE:
            return _fixed_io(K.FIXED_SIZE[kind])
        if kind == K.OBJECT_ID:
            return _fixed_io(idw)
        if kind == K.HANDLE:
            return _handle_io(idw)
        if kind == K.STRING:
            return _string_io(1)
        if kind == K.LSTRING:
            return _string_io(2)
        if kind in (K.OBJECT, K.BASE_OBJECT):
            flagged = self.S.is_managed(oh)
            return (lambda b, p: self._read_inline(b, p, flagged),
                    lambda out, v: self._write_inline(out, v, flagged))
        if kind in (K.OBJECT_PTR, K.BASE_OBJECT_PTR):
            flagged = self.S.is_managed(oh)
            return (lambda b, p: self._read_ptr(b, p, flagged),
                    lambda out, v: self._write_ptr(out, v, flagged))
        if kind == K.REFERENCE:
            return self._read_ref, self._write_ref
        if kind in K.ARRAYS:
            if elem in K.ARRAYS or elem is None:
                raise DecodeError(f"array of {K.KIND_NAMES[elem] if elem is not None else 'bitfield'}")
            ew = K.FIXED_SIZE.get(elem) or (idw if elem == K.OBJECT_ID else None)
            if ew is not None:
                return _fixed_array_io(ew, count if kind == K.STATIC_ARRAY else None)
            er, ewr = self.value_io(elem, None, 0, oh)
            return _array_io(er, ewr, count if kind == K.STATIC_ARRAY else None)
        raise DecodeError(f"no rule for kind {K.KIND_NAMES[kind]}")

    # -- decode ------------------------------------------------------------------------------------------------

    def decode(self, body) -> Root:
        b = bytes(body)
        n = len(b)
        pre = 0
        if n >= 8 and b[0] == 1:
            pre = 12 * _I32.unpack_from(b, 4)[0] + 7
            if pre < 7 or pre + 2 > n:
                raise DecodeError(f"bad pre-header table ({(pre - 7) // 12} entries)", 4)
        p = pre
        h = None
        try:
            if p + 2 + self.id_size + 4 > n:
                raise _short(p, 2 + self.id_size + 4, n)
            status, flag = b[p], b[p + 1]
            p += 2
            oid = b[p:p + self.id_size]
            p += self.id_size
            h = _U32.unpack_from(b, p)[0]
            obj = Obj(h, oid, flag=flag)
            p = self._read_props(b, p + 4, obj)
        except DecodeError as e:
            if h is not None:
                e.path.insert(0, self.S.name_of(h))
            raise
        except (struct.error, IndexError, ValueError, OverflowError) as e:
            raise DecodeError(f"{type(e).__name__}: {e}", p) from None
        if p != n:
            raise DecodeError(f"{n - p} trailing bytes", p, [self.S.name_of(h)])
        return Root(b[:pre], status, obj)

    def _read_props(self, b, p, obj):
        plan = self._plans.get(obj.type_hash) or self.plan(obj.type_hash)
        fields = obj.fields
        name = None
        try:
            for step in plan:
                if step is None:
                    name = "<dyn>"
                    obj.dyn, p = self._read_dyn(b, p)
                else:
                    name, r, _w = step
                    fields[name], p = r(b, p)
        except DecodeError as e:
            e.path.insert(0, name)
            if e.offset is None:
                e.offset = p
            raise
        except (struct.error, IndexError) as e:
            raise DecodeError(f"{type(e).__name__}: {e}", p, [name]) from None
        return p

    def _read_inline(self, b, p, flagged):
        flag = None
        if flagged:
            flag = b[p]
            p += 1
        q = p + self.id_size
        if q + 4 > len(b):
            raise _short(p, self.id_size + 4, len(b))
        h = _U32.unpack_from(b, q)[0]
        obj = Obj(h, b[p:q], flag=flag)
        if h not in self._plans and self.S.type_by_hash(h) is None:
            raise DecodeError(f"unknown type 0x{h:08X}", q, [f"<0x{h:08X}>"])
        return obj, self._read_props(b, q + 4, obj)

    def _read_ptr(self, b, p, flagged):
        s = b[p]
        p += 1
        if s == PTR_NULL:
            return Ptr(s), p
        if s in PTR_LINK:
            q = p + self.id_size
            if q > len(b):
                raise _short(p, self.id_size, len(b))
            return Ptr(s, b[p:q]), q
        if s in PTR_INLINE:
            obj, p = self._read_inline(b, p, flagged)
            return Ptr(s, obj=obj), p
        raise DecodeError(f"unknown pointer status {s}", p - 1)

    def _read_ref(self, b, p):
        tag, extra = b[p], b[p + 1]
        p += 2
        q = p + self.id_size
        if q > len(b):
            raise _short(p, self.id_size, len(b))
        rid = b[p:q]
        if tag != 0:
            return Ref(tag, extra, rid), q
        h = _U32.unpack_from(b, q)[0]
        if self.S.type_by_hash(h) is None:
            raise DecodeError(f"unknown type 0x{h:08X}", q, [f"<0x{h:08X}>"])
        obj = Obj(h, rid)
        return Ref(tag, extra, rid, obj), self._read_props(b, q + 4, obj)

    def _read_dyn(self, b, p):
        n = _U32.unpack_from(b, p)[0]
        p += 4
        if n * 12 > len(b) - p:
            raise DecodeError(f"bad DynamicProperties count {n}", p - 4)
        out = []
        for i in range(n):
            name, oh, tw = struct.unpack_from("<III", b, p)
            p += 12
            kind, elem, count, _bits = K.unpack_type_word(tw)
            r, _w = self.value_io(kind, elem, count, oh)
            try:
                v, p = r(b, p)
            except DecodeError as e:
                e.path.insert(0, f"[{i}]")
                raise
            out.append((name, oh, tw, v))
        return out, p

    # -- encode ------------------------------------------------------------------------------------------------

    def encode(self, root: Root) -> bytes:
        out = bytearray(root.pre_header)
        obj = root.obj
        out.append(root.status)
        out.append(obj.flag if obj.flag is not None else 0)
        self._put_id(out, obj.id)
        out += _U32.pack(obj.type_hash)
        self._write_props(out, obj)
        return bytes(out)

    def _put_id(self, out, i):
        if len(i) != self.id_size:
            raise EncodeError(f"id of {len(i)} bytes, not {self.id_size}")
        out += i

    def _write_props(self, out, obj):
        try:
            plan = self.plan(obj.type_hash)
        except DecodeError as e:
            raise EncodeError(e.message) from None
        fields = obj.fields
        name = None
        try:
            for step in plan:
                if step is None:
                    name = "<dyn>"
                    self._write_dyn(out, obj.dyn or [])
                else:
                    name, _r, w = step
                    w(out, fields[name])
        except EncodeError as e:
            e.path.insert(0, name)
            raise
        except KeyError:
            raise EncodeError(f"{self.S.name_of(obj.type_hash)} has no field {name}") from None
        except (TypeError, AttributeError, struct.error) as e:
            raise EncodeError(f"{type(e).__name__}: {e}", [name]) from None

    def _write_inline(self, out, v, flagged):
        if not isinstance(v, Obj):
            raise EncodeError(f"expected an Obj, got {type(v).__name__}")
        if flagged:
            out.append(v.flag if v.flag is not None else 0)
        self._put_id(out, v.id)
        out += _U32.pack(v.type_hash)
        self._write_props(out, v)

    def _write_ptr(self, out, v, flagged):
        if not isinstance(v, Ptr):
            raise EncodeError(f"expected a Ptr, got {type(v).__name__}")
        s = v.status
        out.append(s)
        if s in PTR_LINK:
            self._put_id(out, v.link)
        elif s in PTR_INLINE:
            self._write_inline(out, v.obj, flagged)
        elif s != PTR_NULL:
            raise EncodeError(f"unknown pointer status {s}")

    def _write_ref(self, out, v):
        if not isinstance(v, Ref):
            raise EncodeError(f"expected a Ref, got {type(v).__name__}")
        out.append(v.tag)
        out.append(v.extra)
        self._put_id(out, v.id)
        if v.tag == 0:
            if v.obj is None:
                raise EncodeError("a tag-0 Ref without its object")
            out += _U32.pack(v.obj.type_hash)
            self._write_props(out, v.obj)

    def _write_dyn(self, out, dyn):
        out += _U32.pack(len(dyn))
        for i, (name, oh, tw, v) in enumerate(dyn):
            out += struct.pack("<III", name, oh, tw)
            kind, elem, count, _bits = K.unpack_type_word(tw)
            _r, w = self.value_io(kind, elem, count, oh)
            try:
                w(out, v)
            except EncodeError as e:
                e.path.insert(0, f"[{i}]")
                raise


# -- value readers / writers ---------------------------------------------------------------------------------------

def _fixed_io(w):
    def read(b, p):
        q = p + w
        if q > len(b):
            raise _short(p, w, len(b))
        return b[p:q], q

    def write(out, v):
        if len(v) != w:
            raise EncodeError(f"value of {len(v)} bytes, not {w}")
        out += v
    return read, write


def _handle_io(idw):
    def read(b, p):
        q = p + 1 + idw
        if q > len(b):
            raise _short(p, 1 + idw, len(b))
        return Handle(b[p], b[p + 1:q]), q

    def write(out, v):
        if not isinstance(v, Handle):
            raise EncodeError(f"expected a Handle, got {type(v).__name__}")
        if len(v.id) != idw:
            raise EncodeError(f"id of {len(v.id)} bytes, not {idw}")
        out.append(v.tag)
        out += v.id
    return read, write


def _string_io(cw):
    """STRING (cw 1) / LSTRING (cw 2, UTF-16): u32 n characters, n * cw bytes and a NUL character; n = 0 has none."""
    nul = bytes(cw)

    def read(b, p):
        n = _U32.unpack_from(b, p)[0]
        p += 4
        if n == 0:
            return b"", p
        q = p + n * cw
        if q + cw > len(b):
            raise _short(p, (n + 1) * cw, len(b))
        if b[q:q + cw] != nul:
            raise DecodeError(f"string of {n} characters not followed by its NUL", q)
        return b[p:q], q + cw

    def write(out, v):
        if len(v) % cw:
            raise EncodeError(f"a UTF-16 string of {len(v)} bytes")
        out += _U32.pack(len(v) // cw)
        if v:
            out += v
            out += nul
    return read, write


def _fixed_array_io(w, static):
    def read(b, p):
        if static is None:
            n = _U32.unpack_from(b, p)[0]
            p += 4
        else:
            n = static
        q = p + n * w
        if q > len(b):
            raise DecodeError(f"array of {n} x {w} bytes, {len(b) - p} left", p)
        if w == 1:
            return [_BYTE[x] for x in b[p:q]], q
        return [b[i:i + w] for i in range(p, q, w)], q

    def write(out, v):
        if static is None:
            out += _U32.pack(len(v))
        elif len(v) != static:
            raise EncodeError(f"static array of {len(v)} elements, not {static}")
        data = b"".join(v)
        if len(data) != len(v) * w:
            bad = next(i for i, x in enumerate(v) if len(x) != w)
            raise EncodeError(f"array element {bad} of {len(v[bad])} bytes, not {w}")
        out += data
    return read, write


def _array_io(er, ew, static):
    def read(b, p):
        if static is None:
            n = _U32.unpack_from(b, p)[0]
            p += 4
            if n > len(b) - p:
                raise DecodeError(f"bad array count {n}", p - 4)
        else:
            n = static
        out = []
        for i in range(n):
            try:
                v, p = er(b, p)
            except DecodeError as e:
                e.path.insert(0, f"[{i}]")
                raise
            out.append(v)
        return out, p

    def write(out, v):
        if static is None:
            out += _U32.pack(len(v))
        elif len(v) != static:
            raise EncodeError(f"static array of {len(v)} elements, not {static}")
        for i, x in enumerate(v):
            try:
                ew(out, x)
            except EncodeError as e:
                e.path.insert(0, f"[{i}]")
                raise
    return read, write
