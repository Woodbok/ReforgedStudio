"""forgelib/objects.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
DIRECTORY_ENTRY_SIZE = 10
OBJECT_HEADER_SIZE = 12

class ObjectError(Exception):
    pass

def walk(d):
    if len(d) < 16:
        raise ObjectError(f'payload too short ({len(d)} bytes)')
    count = struct.unpack_from('<H', d, 0)[0]
    if count == 0:
        raise ObjectError('object count is zero')
    header = 2 + count * DIRECTORY_ENTRY_SIZE + 4
    if header > len(d):
        raise ObjectError(f'directory of {count} runs past the payload')
    entries = []
    o = 2
    for i in range(count):
        serial, size, ffff = struct.unpack_from('<IIH', d, o)
        if ffff != 65535:
            raise ObjectError(f'directory entry {i} lacks its 0xFFFF marker')
        entries.append((serial, size))
        o += DIRECTORY_ENTRY_SIZE
    out = []
    o = header
    for serial, size in entries:
        if o + size > len(d):
            raise ObjectError(f'object at {o} (size {size}) runs past the payload')
        out.append((o, size, serial))
        o += size
    if o != len(d):
        raise ObjectError(f'directory accounts for {o} of {len(d)} bytes')
    return out

def parse_object(d, offset, size):
    if size < OBJECT_HEADER_SIZE:
        raise ObjectError(f'object at {offset} is {size} bytes')
    type_id, _body_size, name_len = struct.unpack_from('<III', d, offset)
    end = offset + OBJECT_HEADER_SIZE + name_len
    if name_len > 1024 or end > offset + size:
        raise ObjectError(f'object at {offset} has a bad name length {name_len}')
    name = d[offset + OBJECT_HEADER_SIZE:end].decode('latin-1')
    return (type_id, name)

def objects(d):
    return [parse_object(d, off, size) for off, size, _serial in walk(d)]

def object_bytes(d, position):
    off, size, _serial = walk(d)[position]
    return d[off:off + size]

def directory_size(d):
    count = struct.unpack_from('<H', d, 0)[0]
    return 2 + count * DIRECTORY_ENTRY_SIZE + 4

def uid_of(obj):
    if len(obj) < OBJECT_HEADER_SIZE:
        return None
    name_len = struct.unpack_from('<I', obj, 8)[0]
    body = OBJECT_HEADER_SIZE + name_len
    if len(obj) < body + 6 or obj[body:body + 2] != b'\x00\x01':
        return None
    return struct.unpack_from('<I', obj, body + 2)[0]

def clip_id_of(obj):
    if len(obj) < OBJECT_HEADER_SIZE:
        return None
    name_len = struct.unpack_from('<I', obj, 8)[0]
    body = OBJECT_HEADER_SIZE + name_len
    if len(obj) < body + 8 or obj[body:body + 2] != b'\x01\x02':
        return None
    at = body + 9 + 12 * struct.unpack_from('<I', obj, body + 4)[0]
    if len(obj) < at + 4:
        return None
    return struct.unpack_from('<I', obj, at)[0]

def identity_of(obj):
    uid = uid_of(obj)
    return uid if uid is not None else clip_id_of(obj)

def directory_mismatches(d):
    out = []
    for pos, (off, size, serial) in enumerate(walk(d)):
        ident = identity_of(d[off:off + size])
        if ident is not None and ident != serial:
            out.append((pos, serial, ident))
    return out

def next_serial(d):
    used = {serial for _off, _size, serial in walk(d)}
    candidate = max(used) + 1 if used else 1
    while candidate in used or candidate > 4294967295:
        candidate += 1
    return candidate

def rebuild(d, replacements=None, additions=None, removals=()):
    replacements = replacements or {}
    bodies = []
    drop = set(removals)
    for pos, (off, size, serial) in enumerate(walk(d)):
        if pos in drop:
            continue
        if pos in replacements:
            new = bytes(replacements[pos])
            ident = identity_of(new)
            bodies.append((ident if ident is not None else serial, new))
        else:
            bodies.append((serial, d[off:off + size]))
    serial = next_serial(d)
    for body in additions or []:
        body = bytes(body)
        ident = identity_of(body)
        if ident is None:
            ident, serial = (serial, serial + 1)
        bodies.append((ident, body))
    out = bytearray()
    out += struct.pack('<H', len(bodies))
    for s, body in bodies:
        out += struct.pack('<IIH', s, len(body), 65535)
    out += struct.pack('<I', 0)
    for _s, body in bodies:
        out += body
    return bytes(out)
