"""forgelib/maps/port/world_list.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import convert
import world
STOCK = world.convert_world

def list_start(body):
    m = world.markers(body)
    k = next((i for i, (_o, n) in enumerate(m) if n == 'FloorHeightData'))
    first = m[k + 1][0]
    at = first - world.COUNT_BEFORE_NAV
    count = struct.unpack_from('<I', body, at)[0]
    assert 8 <= count <= 32 and body[at + 4] == 0, f'no component count before {m[k + 1][1]} ({count})'
    return at

def convert_world(body, mode_remap, taken, keep_escort=None):
    try:
        return STOCK(body, mode_remap, taken, keep_escort)
    except ValueError as e:
        if 'older World layout' not in str(e):
            raise
    ids = convert.class_ids()
    log = ['(world_list.py: the component list starts after FloorHeightData, not at NavigationManager)']
    m = world.markers(body)
    out = bytearray(body)
    at = list_start(body)
    gcwc = next((k for k, n in m if n == 'GameplayCoordinatorWorldComponent'))
    assert at < gcwc, 'the count after the MapEventManager insertion point'
    last_sgm = max((k for k, n in m if n == 'SupportedGameMode'))
    cut = last_sgm + 4 + 2 + 4 + 4
    ending, line = world.tail_ending(body, cut, keep_escort)
    log.append(line)
    out[cut:] = ending
    for k, n in m:
        if n == 'SupportedGameMode':
            old = struct.unpack_from('<I', out, k + 6)[0]
            new = mode_remap.get(old, old)
            if new != old:
                struct.pack_into('<I', out, k + 6, new)
                log.append(f'SupportedGameMode 0x{old:08X} -> 0x{new:08X}')
    uid = world.free_uid(body, struct.unpack_from('<I', body, gcwc - 4)[0] + 256, taken)
    element = struct.pack('<II', uid, ids['MapEventManager']) + world.MAP_EVENT_MANAGER_EMPTY
    out[gcwc - 4:gcwc - 4] = element
    log.append(f'MapEventManager 0x{uid:08X} inserted before GameplayCoordinatorWorldComponent')
    count = struct.unpack_from('<I', out, at)[0]
    struct.pack_into('<I', out, at, count + 1)
    log.append(f'component count {count} -> {count + 1}')
    return (bytes(out), log)
