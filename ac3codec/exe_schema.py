"""AC3's object schema read out of the user's own AC3MP.exe: no schema file, no third-party data.

    python -m ac3codec.exe_schema [AC3MP.exe] [-o model.json] [--names-from DIR ...]

`read_exe(path)` returns the neutral schema model the FastLoad codec reads objects with (ac3codec/schema.py):

    {"types": {type_hash: {"name": str, "base": base_hash or 0, "flags": type flags,
                           "props": [[flags, name_hash, packed_type], ...],
                           "dynamic": True (only on a class whose type flags have 0x2)}},
     "enums": {enum_hash: {"name": str | None, "values": {int: str}}},
     "names": {hash: str}}

How the exe describes its classes (EXE_SCHEMA.md has the evidence):

* Every reflected class has an 80-byte descriptor in .data. At byte offset (words are u32):
      +0 property table (VA)   +4 enum table (VA)   +8 name (VA of an ASCII string)   +12 base type hash
      +16 type hash   +20 instance size   +24 type flags (0x2: a DynamicProperties block follows the class's
      properties)   +40 0x7FFF7FFF   +76 count word: bits 0-15 the number of
      properties, bits 16-23 the number of class-local enums.
  The 0x7FFF7FFF marker with a name pointer in front finds all of them; AC3MP.exe has 3,968.
* The property table is `count` 32-byte records:
      u32 flags, u32 name hash, u32 object hash, u32 type word, u32 field position, u32 getter, u32 setter, u32 0
  -> the model's [flags, name hash, type word << 32 | object hash]. The table is in layout order (bitfield members
  first), not in serialization order.
* The serialization order is the order of the class's property pointers in .data: every record has one static
  pointer to it (a word in .data holding its VA, apart from the descriptor's own +0), and those pointers lie in
  declaration order, which is the order FastLoad reads them in. A record with more than one such pointer (other
  data that names the same property) takes the one nearest the class's others.
* An enum table is `n` packed 10-byte entries {u32 values (VA), u32 enum hash, u16 count}; the values are `count`
  pairs {i32 value, u32 name hash}. Enums no class lists (the global ones) are found as the same entry in .data from
  the hash an enum-kind property names; the enum kind is learnt from the properties that name the classes' own enums.
* Names: a type's name is its descriptor's string. The engine's other names are crc32 hashes of the name; the exe
  keeps only a few thousand of those strings, so `names` holds every model hash whose name the exe's strings (or
  the caller's `extra_names`) hash to, a class-local enum's as "Class::Name". Names are display data and field
  keys only: decoding reads hashes.
"""
import argparse
import array
import json
import os
import re
import struct
import sys
import zlib

DEFAULT_EXE = r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Assassin's Creed III\AC3MP.exe"

MARKER = struct.pack("<I", 0x7FFF7FFF)
MARKER_AT = 40                  # the marker's offset in a descriptor
DESC_WORDS = 20
RECORD = 32
ENUM_ENTRY = 10

BITFIELD = 0x00400000          # type word: a bitfield member (bits 23-27 are then its width, not an element kind)
DYNAMIC = 0x2                   # type flag: the class's serializer appends a DynamicProperties block

_STRING = re.compile(rb"[\x20-\x7e]{1,255}\x00")
_UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){3,255}")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def name_hash(name):
    """The engine's name hash: crc32 of the name."""
    return zlib.crc32(name.encode("latin-1"))


class PE:
    """The sections of a 32-bit PE image, for VA <-> file offset."""

    def __init__(self, data):
        self.data = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("not a PE image")
        count, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        self.base = struct.unpack_from("<I", data, pe + 24 + 28)[0]
        self.sections = []          # (name, rva, size, raw offset, raw size)
        o = pe + 24 + opt_size
        for _ in range(count):
            name = data[o:o + 8].split(b"\0")[0].decode("ascii", "replace")
            vsize, rva, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
            self.sections.append((name, rva, vsize, raw, rsize))
            o += 40

    def off(self, va):
        """-> the file offset of a VA, or None when no section's file bytes hold it."""
        rva = va - self.base
        for _n, sva, vsize, raw, rsize in self.sections:
            if sva <= rva < sva + rsize:
                return raw + rva - sva
        return None

    def va(self, off):
        for _n, sva, _vs, raw, rsize in self.sections:
            if raw <= off < raw + rsize:
                return self.base + sva + off - raw
        return None

    def section(self, name):
        """-> (raw offset, raw size) of the first section called `name`."""
        for n, _sva, _vs, raw, rsize in self.sections:
            if n == name:
                return raw, rsize
        raise ValueError(f"no {name} section")

    def cstring(self, va, limit=256):
        o = self.off(va)
        if o is None:
            return None
        e = self.data.find(b"\0", o, o + limit)
        if e <= o:
            return None
        s = self.data[o:e]
        return s.decode("ascii") if all(32 <= c < 127 for c in s) else None


class Descriptor:
    __slots__ = ("off", "va", "table", "enum_table", "name", "base", "type_hash", "size", "flags", "count",
                 "enum_count", "records", "order", "enum_hashes")

    def __init__(self, pe, off, words):
        self.off, self.va = off, pe.va(off)
        self.table, self.enum_table = words[0], words[1]
        self.base, self.type_hash, self.size, self.flags = words[3], words[4], words[5], words[6]
        self.count, self.enum_count = words[19] & 0xFFFF, (words[19] >> 16) & 0xFF
        self.name = pe.cstring(words[2])
        self.records = []           # (record VA, (flags, name hash, packed type))
        self.order = None           # how the serialization order was found
        self.enum_hashes = []


def find_descriptors(pe):
    """-> [Descriptor] for every reflected class in the image."""
    b, out, k = pe.data, [], pe.data.find(MARKER)
    while k >= 0:
        d = k - MARKER_AT
        if k % 4 == 0 and d >= 0:
            w = struct.unpack_from(f"<{DESC_WORDS}I", b, d)
            if w[4] and pe.cstring(w[2]) is not None and (w[0] == 0 or pe.off(w[0]) is not None) \
                    and (w[1] == 0 or pe.off(w[1]) is not None) and (w[0] or not w[19] & 0xFFFF):
                out.append(Descriptor(pe, d, w))
        k = b.find(MARKER, k + 1)
    return out


def read_records(pe, desc):
    o = pe.off(desc.table) if desc.table else None
    for i in range(desc.count if o is not None else 0):
        flags, name, obj, tword, _pos, _get, _set, zero = struct.unpack_from("<8I", pe.data, o + RECORD * i)
        desc.records.append((desc.table + RECORD * i, (flags, name, (tword << 32) | obj)))


def pointer_index(pe, wanted):
    """-> {VA: [file offsets of the .data words holding it]} for the VAs in `wanted`."""
    raw, size = pe.section(".data")
    words = array.array("I", pe.data[raw:raw + size // 4 * 4])
    if sys.byteorder != "little":
        words.byteswap()
    out = {}
    for i, v in enumerate(words):
        if v in wanted:
            out.setdefault(v, []).append(raw + 4 * i)
    return out


def serial_order(desc, refs):
    """Put desc.records in serialization order: by the .data pointer to each record (see the module doc)."""
    if len(desc.records) < 2:
        desc.order = "single" if desc.records else "empty"
        return
    own = {desc.off}
    cand = [[o for o in refs.get(va, ()) if o not in own] for va, _p in desc.records]
    if not all(cand):
        desc.order = "table"            # never on AC3MP.exe: a record without a pointer keeps the table's order
        return
    single = sorted(c[0] for c in cand if len(c) == 1)
    if len(single) < len(cand):
        if single:
            centre = single[len(single) // 2]
        else:                           # no record with one pointer: centre on the class's own pointer list
            dp = refs.get(desc.va, [])
            centre = dp[0] if dp else min(min(c) for c in cand)
        cand = [[min(c, key=lambda o: abs(o - centre))] for c in cand]
        desc.order = "pointers (nearest)"
    else:
        desc.order = "pointers"
    at = [c[0] for c in cand]
    desc.records = [r for _a, r in sorted(zip(at, desc.records), key=lambda x: x[0])]


def read_enum(pe, off):
    """-> (enum hash, [(value, name hash)]) of the 10-byte entry at file offset off, or None."""
    if off + ENUM_ENTRY > len(pe.data):
        return None
    values, h, count = struct.unpack_from("<IIH", pe.data, off)
    vo = pe.off(values) if values else None
    if vo is None or not 0 < count < 4096 or vo + 8 * count > len(pe.data):
        return None
    return h, [struct.unpack_from("<iI", pe.data, vo + 8 * i) for i in range(count)]


def enum_kind(types, local):
    """-> the property kind (type word bits 16-20) that names an enum: learnt from the properties that name one of
    the classes' own enums (25 on AC3MP.exe)."""
    from collections import Counter
    c = Counter(((p >> 48) & 0x1F) for t in types.values() for _f, _n, p in t["props"] if p & 0xFFFFFFFF in local)
    return c.most_common(1)[0][0] if c else None


def enum_refs(types, kind):
    """-> {enum hash} named by a property of that kind, or by an array of them (its element kind, bits 23-27 when
    the bitfield bit 22 is clear, is that kind)."""
    out = set()
    for t in types.values():
        for _f, _n, packed in t["props"]:
            tw, obj = packed >> 32, packed & 0xFFFFFFFF
            elem = None if tw & BITFIELD else (tw >> 23) & 0x1F
            if obj and kind is not None and ((tw >> 16) & 0x1F == kind or elem == kind):
                out.add(obj)
    return out


def find_enum(pe, h, region):
    """-> [(value, name hash)] of the enum entry with hash h in the file range `region`, or None (or the first of
    several that disagree - never on AC3MP.exe)."""
    b, lo, hi = pe.data, region[0], region[0] + region[1]
    pat, k, found = struct.pack("<I", h), b.find(struct.pack("<I", h), lo, hi), []
    while k >= 0:
        r = read_enum(pe, k - 4) if k >= 4 else None
        if r and r[1] not in found:
            found.append(r[1])
        k = b.find(pat, k + 1, hi)
    return found[0] if found else None


def exe_strings(data):
    """-> {text} of the image's ASCII and UTF-16 strings and the identifiers inside them."""
    out = {m.group()[:-1].decode("ascii") for m in _STRING.finditer(data)}
    out |= {m.group().decode("utf-16-le") for m in _UTF16.finditer(data)}
    out |= set(_IDENT.findall("\n".join(out)))
    return out


def source_names(*roots, exts=(".py", ".cpp", ".h", ".hpp", ".inc", ".json", ".txt", ".md")):
    """-> {identifier} in the text files under `roots`: names a caller's own code uses, to pass as extra_names."""
    out = set()
    for root in roots:
        files = [root] if os.path.isfile(root) else [
            os.path.join(d, f) for d, _ds, fs in os.walk(root) for f in fs if f.lower().endswith(exts)]
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                out.update(_IDENT.findall(fh.read()))
    return out


def resolve_names(model, descs, strings):
    """Fill model["names"] (and each enum's name / value names) from the descriptors and `strings`."""
    names = {d.type_hash: d.name for d in descs}
    wanted = set()
    for t in model["types"].values():
        wanted.add(t["base"])
        for _f, n, packed in t["props"]:
            wanted.update((n, packed & 0xFFFFFFFF))
    for h, e in model["enums"].items():
        wanted.add(h)
        wanted.update(vh for _v, vh in e["raw"])
    wanted -= set(names)
    wanted.discard(0)
    by_hash = {}
    for s in strings:
        h = name_hash(s) if s.isascii() else None
        if h in wanted:
            by_hash.setdefault(h, set()).add(s)
    for h, ss in by_hash.items():
        names[h] = min(ss)          # crc32 collisions between two candidate strings: never on AC3MP.exe
    # A class-local enum is named "Class::Name": try every identifier after its class's name.
    idents = sorted({s.encode("ascii") for s in strings if s.isascii() and _IDENT.fullmatch(s) and len(s) < 80})
    local = {}
    for d in descs:
        for h in d.enum_hashes:
            if h not in names and d.name:
                local.setdefault(d.name, []).append(h)
    crc = zlib.crc32
    for cls, hs in local.items():
        seed, todo = crc((cls + "::").encode("ascii")), set(hs)
        for s in idents:
            c = crc(s, seed)
            if c in todo:
                names[c] = f"{cls}::{s.decode('ascii')}"
                todo.discard(c)
                if not todo:
                    break
    model["names"] = {h: s for h, s in names.items() if h in wanted or h in model["types"]}
    for h, e in model["enums"].items():
        e["name"] = names.get(h)
        e["values"] = {v: names.get(vh, f"0x{vh:08X}") for v, vh in e.pop("raw")}


def read_exe(path=DEFAULT_EXE, extra_names=(), report=None):
    """-> the neutral schema model of the exe at `path` (module doc). `extra_names`: more candidate strings for
    `names` (each is kept only if its hash is one the model uses). `report`, a dict, receives how each part was
    found (descriptor count, order methods, enums not found)."""
    with open(path, "rb") as fh:
        pe = PE(fh.read())
    descs = find_descriptors(pe)
    for d in descs:
        read_records(pe, d)
    recs = {va for d in descs for va, _p in d.records} | {d.va for d in descs}
    refs = pointer_index(pe, recs)
    for d in descs:
        serial_order(d, refs)
    types = {}
    for d in descs:
        if d.type_hash in types:
            raise ValueError(f"two descriptors for type {d.type_hash:#010x}")
        types[d.type_hash] = {"name": d.name, "base": d.base, "flags": d.flags,
                              "props": [list(p) for _va, p in d.records]}
        if d.flags & DYNAMIC:
            types[d.type_hash]["dynamic"] = True
    enums = {}
    for d in descs:
        o = pe.off(d.enum_table) if d.enum_table else None
        d.enum_hashes = []
        for i in range(d.enum_count if o is not None else 0):
            r = read_enum(pe, o + ENUM_ENTRY * i)
            if r:
                enums.setdefault(r[0], {"raw": r[1]})
                d.enum_hashes.append(r[0])
    missing = []
    region = pe.section(".data")
    for h in sorted(enum_refs(types, enum_kind(types, set(enums))) - set(enums)):
        vals = find_enum(pe, h, region)
        if vals is None:
            missing.append(h)
        else:
            enums[h] = {"raw": vals}
    model = {"types": types, "enums": enums, "names": {}}
    resolve_names(model, descs, exe_strings(pe.data) | set(extra_names))
    if report is not None:
        from collections import Counter
        report.update({"descriptors": len(descs), "order": dict(Counter(d.order for d in descs)),
                       "enums": len(enums), "enums_not_found": missing, "names": len(model["names"]),
                       "nearest": sorted(d.name for d in descs if d.order == "pointers (nearest)"),
                       "table_order": sorted(d.name for d in descs if d.order == "table")})
    return model


def to_json(model):
    """-> the model with string keys ("0x1A2B3C4D"; an enum value's decimal), sorted - the codec's JSON form."""
    k = lambda h: f"0x{h:08X}"
    return {"types": {k(h): model["types"][h] for h in sorted(model["types"])},
            "enums": {k(h): {"name": e["name"], "values": {str(v): s for v, s in sorted(e["values"].items())}}
                      for h, e in sorted(model["enums"].items())},
            "names": {k(h): model["names"][h] for h in sorted(model["names"])}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("exe", nargs="?", default=DEFAULT_EXE)
    ap.add_argument("-o", "--out", help="write the model as JSON here")
    ap.add_argument("--names-from", nargs="*", default=(), help="files / folders whose identifiers name hashes too")
    a = ap.parse_args(argv)
    rep = {}
    model = read_exe(a.exe, source_names(*a.names_from) if a.names_from else (), rep)
    props = sum(len(t["props"]) for t in model["types"].values())
    print(f"{os.path.basename(a.exe)}: {len(model['types'])} types, {props} properties, {len(model['enums'])} enums, "
          f"{len(model['names'])} names")
    print("  order:", rep["order"])
    if rep["enums_not_found"]:
        print("  enums named by a property but not in the exe:", [f"0x{h:08X}" for h in rep["enums_not_found"]])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(to_json(model), fh, indent=0, separators=(",", ":"))
        print("  ->", a.out)


if __name__ == "__main__":
    main()
