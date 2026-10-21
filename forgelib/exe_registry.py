"""forgelib/exe_registry.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import struct
import sys
from collections import Counter
from exe_classes import PE
NAME_RE = re.compile('^[A-Za-z_][A-Za-z0-9_:]{2,80}$')

def string_table(pe):
    out = {}
    for m in re.finditer(b'[ -~]{3,96}\\x00', pe.data):
        s = m.group()[:-1].decode('ascii')
        if not NAME_RE.match(s):
            continue
        va = pe.va(m.start())
        if va is not None:
            out[va] = s
    return out

def registry(pe):
    names = string_table(pe)
    data = pe.data
    out = {}
    collisions = []
    for name, sva, vsize, raw, rawsize in pe.sections:
        if name not in ('.data', '.rdata'):
            continue
        end = min(raw + rawsize, len(data)) - 16
        for off in range(raw, end, 4):
            ptr = struct.unpack_from('<I', data, off)[0]
            text = names.get(ptr)
            if text is None:
                continue
            parent, class_id, size = struct.unpack_from('<III', data, off + 4)
            if class_id in (0, 4294967295) or size > 65536:
                continue
            if class_id < 4096:
                continue
            if class_id in out and out[class_id] != text:
                collisions.append((class_id, out[class_id], text))
            out[class_id] = text
    return (out, collisions)
_LOADED = {}

def load(path):
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    if key not in _LOADED:
        pe = PE(path)
        _LOADED[key] = registry(pe)
    reg, collisions = _LOADED[key]
    return (dict(reg), list(collisions))
import paths as _paths
AC3 = _paths.DEFAULT_EXE
ACR = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Assassin's Creed Revelations\\ACRMP.exe"

def main():
    for label, path in (('ACR', os.environ.get('ACRMP_EXE', ACR)), ('AC3', os.environ.get('AC3MP_EXE', AC3))):
        reg, collisions = load(path)
        print(f'{label}: {len(reg)} class ids, {len(collisions)} collisions')
        for probe in (1331775283, 1096652136, 2729961751, 585940579, 2535097390, 291784361, 1098987332, 2244483011):
            print(f"   0x{probe:08X}  {reg.get(probe, '-')}")
if __name__ == '__main__':
    main()
