"""forgelib/class_sizes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import re
import struct
import sys
from exe_classes import PE
import exe_registry

def sized_registry(path):
    pe = PE(path)
    names = exe_registry.string_table(pe)
    out = {}
    for off in exe_registry.record_offsets(pe, names):
        text = names[struct.unpack_from('<I', pe.data, off)[0]]
        parent, class_id, size = struct.unpack_from('<III', pe.data, off + 4)
        if class_id < 4096 or class_id == 4294967295 or size > 65536:
            continue
        out[class_id] = (text, size)
    return out

def main():
    pat = re.compile(sys.argv[1]) if len(sys.argv) > 1 else re.compile('(?i)anim|skeleton|bone|pose|ragdoll|character|cloth|mesh|texture')
    acr = sized_registry(exe_registry.ACR)
    ac3 = sized_registry(exe_registry.AC3)
    shared = set(acr) & set(ac3)
    changed = [(i, acr[i][0], acr[i][1], ac3[i][1]) for i in shared if acr[i][0] == ac3[i][0] and acr[i][1] != ac3[i][1]]
    print(f'{len(shared)} classes in both; {len(changed)} changed size')
    hits = sorted((c for c in changed if pat.search(c[1])), key=lambda c: c[1])
    print(f'\n-- size changed, matching {pat.pattern} ({len(hits)}) --')
    for i, n, a, b in hits:
        print(f'  0x{i:08X}  {n:<40} {a:>5} -> {b:>5}')
    same = sorted((i for i in shared if acr[i][0] == ac3[i][0] and acr[i][1] == ac3[i][1] and pat.search(acr[i][0])))
    print(f'\n-- unchanged, matching the same filter: {len(same)} classes --')
    for i in same[:40]:
        print(f'  0x{i:08X}  {acr[i][0]:<40} {acr[i][1]:>5}')
    if len(same) > 40:
        print(f'  ... and {len(same) - 40} more')
if __name__ == '__main__':
    main()
