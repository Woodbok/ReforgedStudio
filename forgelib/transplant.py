"""forgelib/transplant.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
OBJECT_HEADER_SIZE = 12
UID_OFFSET_IN_BODY = 2

class TransplantError(Exception):
    pass

def object_at(d, offset, size):
    type_id, body_size, name_len = struct.unpack_from('<III', d, offset)
    body_at = offset + OBJECT_HEADER_SIZE + name_len
    return {'offset': offset, 'size': size, 'type': type_id, 'body_size': body_size, 'name': d[offset + OBJECT_HEADER_SIZE:body_at].decode('latin-1'), 'body_at': body_at, 'uid': struct.unpack_from('<I', d, body_at + UID_OFFSET_IN_BODY)[0], 'body': bytes(d[body_at:offset + size])}

def index(d, walk):
    out = {}
    for offset, size, _serial in walk(d):
        obj = object_at(d, offset, size)
        out[obj['uid']] = obj
    return out

def references(obj, uids):
    body = obj['body']
    found = {}
    for p in range(len(body) - 3):
        v = struct.unpack_from('<I', body, p)[0]
        if v in uids and v != obj['uid']:
            found[v] = found.get(v, 0) + 1
    return found

def remap(body, mapping):
    out = bytearray(body)
    replaced = {}
    for old, new in mapping.items():
        if old == new:
            continue
        old_b = struct.pack('<I', old)
        new_b = struct.pack('<I', new)
        start = 0
        while True:
            i = out.find(old_b, start)
            if i < 0:
                break
            out[i:i + 4] = new_b
            replaced[old] = replaced.get(old, 0) + 1
            start = i + 4
    return (bytes(out), replaced)

def build_object(type_id, body_size, name, body):
    encoded = name.encode('latin-1')
    return struct.pack('<III', type_id, body_size, len(encoded)) + encoded + body

def transplant(donor, host, uid_mapping):
    body, replaced = remap(donor['body'], uid_mapping)
    body = body[:UID_OFFSET_IN_BODY] + struct.pack('<I', host['uid']) + body[UID_OFFSET_IN_BODY + 4:]
    if donor['type'] != host['type']:
        raise TransplantError(f"type mismatch: donor 0x{donor['type']:08X} into host 0x{host['type']:08X}")
    return (build_object(host['type'], donor['body_size'], host['name'], body), replaced)
