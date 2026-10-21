"""forgelib/roster.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
ROSTER_TYPE = 2110710229
KIT_TYPE = 85324792
LISTS_AT = 26
ENTRY_PREFIX = b'\x01\x00'
ENTRY_SIZE = 6

class RosterError(Exception):
    pass

def parse(body):
    header = bytes(body[:LISTS_AT])
    lists = []
    pos = LISTS_AT
    while pos < len(body):
        if pos + 4 > len(body):
            raise RosterError(f'truncated count at +{pos}')
        count = struct.unpack_from('<I', body, pos)[0]
        pos += 4
        if pos + count * ENTRY_SIZE > len(body):
            raise RosterError(f'list at +{pos - 4} claims {count} entries, past the end of the body')
        uids = []
        for i in range(count):
            if body[pos:pos + 2] != ENTRY_PREFIX:
                raise RosterError(f'entry {i} of list at +{pos - 4 - i * ENTRY_SIZE} lacks its 01 00 prefix (+{pos})')
            uids.append(struct.unpack_from('<I', body, pos + 2)[0])
            pos += ENTRY_SIZE
        lists.append(uids)
    return (header, lists)

def unparse(header, lists):
    out = bytearray(header)
    for uids in lists:
        out += struct.pack('<I', len(uids))
        for uid in uids:
            out += ENTRY_PREFIX + struct.pack('<I', uid)
    return bytes(out)

def kits(body):
    return parse(body)[1][0]

def with_kits(body, new_kits):
    header, lists = parse(body)
    lists[0] = list(new_kits)
    out = unparse(header, lists)
    parse(out)
    return out

def add_kit(body, uid):
    ks = kits(body)
    if uid in ks:
        raise RosterError(f'kit 0x{uid:08X} is already in the roster')
    return with_kits(body, ks + [uid])

def remove_kit(body, uid):
    ks = kits(body)
    if uid not in ks:
        raise RosterError(f'kit 0x{uid:08X} is not in the roster')
    return with_kits(body, [k for k in ks if k != uid])

def build(name, body):
    from transplant import build_object
    return build_object(ROSTER_TYPE, len(body) - 1, name, body)

def selftest(plain, verbose=True):
    from objects import walk
    from transplant import object_at
    objs = [object_at(plain, o, s) for o, s, _ in walk(plain)]
    by_uid = {o['uid']: o for o in objs}
    roster = next((o for o in objs if o['type'] == ROSTER_TYPE))
    body = roster['body']
    fails = 0

    def check(cond, what):
        nonlocal fails
        if not cond:
            fails += 1
        if verbose:
            print(f"  {('ok  ' if cond else 'FAIL')} {what}")
    header, lists = parse(body)
    check(unparse(header, lists) == body, 'parse/unparse round-trips the body')
    check(len(lists) == 3, f'three lists ({[len(l) for l in lists]})')
    check(all((u in by_uid for l in lists for u in l)), 'every listed uid resolves to an object in the payload')
    check(all((by_uid[u]['type'] == KIT_TYPE for u in lists[0])), 'list 1 is all CharacterBuildingKit')
    check(len({by_uid[u]['type'] for u in lists[1] + lists[2]}) == 1, 'lists 2 and 3 share one class')
    check(roster['body_size'] == len(body) - 1, 'bodySize == len(body) - 1')
    check(with_kits(body, kits(body)) == body, 'with_kits is the identity')
    last = lists[0][-1]
    shrunk = remove_kit(body, last)
    check(len(shrunk) == len(body) - 6 and parse(shrunk)[1][1:] == lists[1:], 'remove_kit drops 6 bytes and leaves the other lists intact')
    grown = add_kit(shrunk, last)
    check(grown == body, 'add_kit after remove_kit restores the body exactly')
    n = len(lists[0])
    old_grow = bytearray(body)
    old_grow[32 + n * 6:32 + n * 6] = struct.pack('<IH', last, 1)
    struct.pack_into('<I', old_grow, 26, n + 1)
    old_shrink = bytearray(body)
    del old_shrink[32 + (n - 1) * 6:32 + n * 6]
    struct.pack_into('<I', old_shrink, 26, n - 1)
    for label, bad in (('grow at +32+6n', old_grow), ('shrink at +32+6i', old_shrink)):
        try:
            parse(bytes(bad))
            check(False, f'the old {label} edit is rejected')
        except RosterError as e:
            check(True, f'the old {label} edit is rejected: {e}')
    return fails
if __name__ == '__main__':
    import os
    import sys
    from forge import Forge
    from datafile import decompress
    from paths import AC3_SOURCE
    with Forge(os.path.join(AC3_SOURCE, 'DataPC.forge')) as f:
        rec = next((r for r in f.records if r.name == 'Game Bootstrap Settings'))
        plain = decompress(f.read(rec))
    fails = selftest(plain)
    print(f'{fails} failures')
    sys.exit(1 if fails else 0)
