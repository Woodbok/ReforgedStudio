"""forgelib/ac4/world_rules.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import pickle
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, os.path.join(common.ROOT, 'maps'))
import convert
import world
IDS = convert.class_ids()
CID = {n: struct.pack('<I', IDS[n]) for n in ('World', 'GridLayout', 'GridPartition', 'GridCell', 'WorldDataLayerManager', 'DataLayerAssociation', 'WorldGraphicData', 'AOSettings', 'WorldParticleSystem', 'WorldLightMapSettings', 'WorldEngineData', 'FloorHeightData', 'NavigationManager', 'HorseGameplayCoordinatorWorldComponent', 'TraineeGameplayCoordinatorWorldComponent', 'EagleVisionManager', 'MapManager', 'PropertyController', 'MapEventManager', 'TimeOfDayManager')}
LEAF_BYTE = 95
AO_HEAD = CID['AOSettings'] + struct.pack('<f', 12.0) + b'\x01\x01\x01\x01'
LIST_END = bytes.fromhex('03000000000000000000')
WLMS_47 = CID['WorldLightMapSettings'] + bytes.fromhex('000000004ca6aa3e00002042000048420001010101cdcccc3d00009644000000c8410000a0400100000000')
PC_INSERT = bytes.fromhex('080000000000000009000000') + bytes(36)
NULL_PTR = bytes.fromhex('030000000000')
TOP_LEVEL = {'NavigationManager', 'RentAVehicleWorldComponent', 'PickupXMLManager', 'FightManager', 'TargetingManager', 'SoundBankWorldComponent', 'HorseGameplayCoordinatorWorldComponent', 'TraineeGameplayCoordinatorWorldComponent', 'EagleVisionManager', 'MapManager', 'TimeOfDayManager', 'BlobSettings', 'MapEventManager', 'GameplayCoordinatorWorldComponent', 'UIWorldComponent', 'SpawnSettings', 'GameModeInfo', 'DLCWorldComponent', 'WarmUpWorldComponent', 'PhotoBoothWorldComponent'}

class WorldError(Exception):
    pass

def u32(b, at):
    return struct.unpack_from('<I', b, at)[0]

def convert_world(body, *, keep_wps=False, keep_events=False, fakes=True, trainee_uids=None, drop_layouts=(), layer_filter=None):
    log = []
    segs = world.segments(body)
    names = [n for n, _ in segs]
    if names[:4] != ['(header)', 'World', 'GridLayout', 'GridPartition'] or 'GridCell' not in names:
        raise WorldError(f'not an AC4 World with an inline grid: {names[:6]}')
    out = bytearray()
    head, wseg = (segs[0][1], bytearray(segs[1][1]))
    n_areas = u32(wseg, 8)
    at = 12 + 6 * n_areas + 10
    if wseg[12 + 6 * n_areas:at] != NULL_PTR + bytes(4):
        raise WorldError(f"World segment: unexpected bytes before the FakeEntities pointer: {wseg[12 + 6 * n_areas:at].hex(' ')}")
    if wseg[at] == 0 and len(wseg) == at + 5 + 6 + 6 + 8:
        fakes_uid = u32(wseg, at + 1)
        rest = wseg[at + 5:]
        ptr = b'\x01\x00' + struct.pack('<I', fakes_uid) if fakes else NULL_PTR
        wseg = wseg[:at] + ptr + rest
        log.append(f"World: FakeEntities 0x{fakes_uid:08X} -> {('01 00 uid' if fakes else 'null (no stand-in tiles)')}")
    elif wseg[at:at + 2] == b'\x01\x00' or wseg[at:at + 6] == NULL_PTR:
        log.append("World: FakeEntities pointer already in AC3's form")
    else:
        raise WorldError(f"World segment: FakeEntities pointer unrecognised: {wseg[at:at + 8].hex(' ')}")
    out += head + wseg
    gl = bytearray(segs[2][1])
    if len(gl) != 32 or gl[-6:-4] != b'\x00\x01':
        raise WorldError(f"GridLayout not in AC4's inline form: {gl.hex(' ')}")
    old_leaf = gl[4]
    gl[4] = LEAF_BYTE
    grid_uid = u32(gl, 28)
    gl[-6:-4] = b'\x01\x00'
    out += gl
    gp_seg = segs[3][1]
    levels, nodes = (u32(gp_seg, 4), u32(gp_seg, 8))
    k = 4
    cells = []
    while k < len(segs) and segs[k][0] == 'GridCell':
        cells.append(segs[k][1])
        k += 1
    if len(cells) != nodes:
        raise WorldError(f'GridPartition says {nodes} nodes, {len(cells)} GridCells inline')
    last = bytearray(cells[-1])
    leaves = u32(last, 13)
    if len(last) != 17 + leaves:
        raise WorldError(f'last GridCell: {len(last)} bytes for {leaves} leaves')
    last[17:] = bytes([LEAF_BYTE]) * leaves
    grid_obj = b'\x00\x01' + struct.pack('<I', grid_uid) + gp_seg + b''.join(cells[:-1]) + bytes(last)
    log.append(f'GridPartition object: {levels} levels, {nodes} nodes, {leaves} leaves; leaf byte 0x{old_leaf:02X} -> 0x{LEAF_BYTE:02X}')
    if segs[k][0] != 'GridPartition' or segs[k + 1][0] != 'WorldDataLayerManager':
        raise WorldError(f'after the grid: {segs[k][0]}, {segs[k + 1][0]}')
    comp = segs[k][1]
    if len(comp) != 10 or comp[4:6] != b'\x00\x01':
        raise WorldError(f"GridPartition component: {comp.hex(' ')}")
    wdlm_uid = u32(comp, 6)
    wd = segs[k + 1][1]
    count = u32(wd, 4)
    k += 2
    entries = []
    while k < len(segs) and segs[k][0] == 'DataLayerAssociation':
        entries.append(segs[k][1])
        k += 1
    if len(entries) != count:
        raise WorldError(f'WorldDataLayerManager says {count} entries, {len(entries)} inline')
    tail = entries[-1][14:]
    if len(tail) != 5 or tail[0] != 0:
        raise WorldError(f"after the layer entries: {tail.hex(' ')}")
    layers_obj = b'\x00\x01' + struct.pack('<I', wdlm_uid) + wd + b''.join(entries[:-1]) + entries[-1][:14]
    assert len(layers_obj) == 14 + 18 * count, (len(layers_obj), count)
    out += CID['GridPartition'] + b'\x01\x00' + struct.pack('<I', wdlm_uid) + tail
    log.append(f'WorldDataLayerManager object: {count} entries')
    if segs[k][0] != 'WorldGraphicData':
        raise WorldError(f'expected WorldGraphicData, got {segs[k][0]}')
    out += segs[k][1]
    k += 1
    ao = segs[k][1]
    if segs[k][0] != 'AOSettings' or ao[4:6] != b'\x01\x00' or ao[10] != 0 or any((b > 1 for b in ao[11:20])) or (ao[20] != 0):
        raise WorldError(f"AOSettings not in AC4's form: {ao[:32].hex(' ')}")
    wps_count = u32(ao, 21)
    k += 1
    wps = []
    while k < len(segs) and segs[k][0] == 'WorldParticleSystem':
        wps.append(segs[k][1])
        k += 1
    if segs[k][0] != 'WorldEngineData':
        raise WorldError(f'expected WorldEngineData after the particle systems, got {segs[k][0]}')
    if len(wps) != wps_count:
        raise WorldError(f'AOSettings counts {wps_count} particle systems, {len(wps)} follow')
    if keep_wps and wps:
        if ao[25] != 4 or ao[26] != 0:
            raise WorldError(f"AOSettings tail: {ao[21:].hex(' ')}")
        out += AO_HEAD + struct.pack('<I', wps_count) + ao[26:]
        for i, s in enumerate(wps):
            n = u32(s, 9)
            p = 13 + 5 * n
            if s[p:p + 2] != b'\x01\x00':
                raise WorldError(f"WorldParticleSystem {i}: no prototype reference at +{p}: {s[p:p + 8].hex(' ')}")
            fx = s[p + 6:p + 8]
            if i < len(wps) - 1:
                if s[p + 8:p + 13] != bytes.fromhex('0000000004'):
                    raise WorldError(f"WorldParticleSystem {i}: tail {s[p + 8:].hex(' ')}")
                out += s[:p + 8] + s[p + 13:]
            else:
                out += s[:p + 8] + LIST_END
        log.append(f"{len(wps)} WorldParticleSystems kept (5 bytes off each tail; AC4's lighting lists after the last dropped)")
    else:
        out += AO_HEAD + bytes(4) + LIST_END
        log.append(f"{len(wps)} WorldParticleSystems dropped (AOSettings in Whiteroom's 26-byte form)")
    out += WLMS_47
    log.append("WorldLightMapSettings: Mill's 47-byte form (no lightmap object) inserted")
    rest = segs[k:]
    rest_names = [n for n, _ in rest]
    fh = rest_names.index('FloorHeightData')
    horse = rest_names.index('HorseGameplayCoordinatorWorldComponent')
    pc = rest_names.index('PropertyController')
    if 'TraineeGameplayCoordinatorWorldComponent' in rest_names or 'EagleVisionManager' in rest_names:
        raise WorldError('Trainee / EagleVision already present: not an AC4 World')
    pieces = []
    for i, (n, s) in enumerate(rest):
        if i == horse:
            if len(s) != 10 or s[4:6] != b'\x03\x00':
                raise WorldError(f"Horse coordinator: {s.hex(' ')}")
            nxt = u32(s, 6)
            t_uid, e_uid = trainee_uids or (nxt - 2, nxt - 1)
            s = s[:6] + struct.pack('<I', t_uid) + CID['TraineeGameplayCoordinatorWorldComponent'] + b'\x03\x00' + struct.pack('<I', e_uid) + CID['EagleVisionManager'] + b'\x00' + struct.pack('<I', nxt)
            log.append(f'Trainee 0x{t_uid:08X} and EagleVision 0x{e_uid:08X} inserted after the horse coordinator')
        elif i == pc:
            if len(s) != 66 or s[14:62] != PC_INSERT:
                raise WorldError(f"PropertyController {len(s)} B: {s[14:62].hex(' ')}")
            s = s[:14] + s[62:]
            log.append("PropertyController: AC4's 48 inserted bytes removed")
        elif n == 'RegionLayoutManager' and drop_layouts:
            cnt = u32(s, 4)
            keep_e = [s[8 + 6 * j:14 + 6 * j] for j in range(cnt) if u32(s, 10 + 6 * j) not in set(drop_layouts)]
            if s[8 + 6 * cnt - 6:8 + 6 * cnt][:2] != b'\x01\x00':
                raise WorldError(f"RegionLayoutManager: entries not `01 00 uid`: {s[8:8 + 6 * cnt].hex(' ')}")
            s = s[:4] + struct.pack('<I', len(keep_e)) + b''.join(keep_e) + s[8 + 6 * cnt:]
            log.append(f'RegionLayoutManager: {cnt - len(keep_e)} of {cnt} layouts dropped')
        elif n == 'MapEventManager' and (not keep_events):
            cnt = u32(s, 4)
            s = s[:4] + bytes(4) + s[8 + 6 * cnt:]
            if len(s) != 13:
                raise WorldError(f'MapEventManager: {len(s)} bytes after emptying')
            log.append(f'MapEventManager: {cnt} events dropped')
        pieces.append(s)
    top = sum((1 for n, _ in rest if n in TOP_LEVEL)) + 2
    fh_seg = bytearray(pieces[fh])
    at = len(fh_seg) - world.COUNT_BEFORE_NAV
    old = u32(fh_seg, at)
    if fh_seg[at + 4] != 0 or not 8 <= old <= 32:
        raise WorldError(f'FloorHeightData tail: count field reads {old}')
    struct.pack_into('<I', fh_seg, at, top)
    pieces[fh] = bytes(fh_seg)
    log.append(f'component count {old} -> {top}')
    out += b''.join(pieces)
    return (bytes(out), grid_obj, layers_obj, log)
CONTENT_OK = {'FloorHeightData', 'GridLayout', 'RegionLayoutManager', 'SoundBankDependencies', 'WwiseID', 'SpawnSettings', 'MapEventManager', 'WorldParticleSystem', 'AOSettings', 'PropertyController', 'SupportedGameMode', 'GameModeInfo', 'WorldGraphicData', 'FightManagerData', 'World', 'MapManager', 'GridPartition', 'WorldLightMapSettings', 'WorldLightMapObject', 'DLCWorldInfo', 'UIString', 'SoundBank', 'SoundDescriptor'}

def compare(ac3, ours):
    import difflib
    a, b = (world.segments(ac3), world.segments(ours))
    na, nb = ([n for n, _ in a], [n for n, _ in b])
    rows, exact, content, mismatch = ([], 0, 0, 0)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, na, nb, autojunk=False).get_opcodes():
        if tag == 'equal':
            for i, j in zip(range(i1, i2), range(j1, j2)):
                sa, sb = (a[i][1], b[j][1])
                if sa == sb:
                    exact += 1
                    rows.append((na[i], 'exact', len(sa), len(sb)))
                elif len(sa) == len(sb) or na[i] in CONTENT_OK:
                    content += 1
                    rows.append((na[i], 'content', len(sa), len(sb)))
                else:
                    mismatch += 1
                    rows.append((na[i], 'MISMATCH', len(sa), len(sb)))
        else:
            for i in range(i1, i2):
                if na[i] in ('WorldLightMapObject', 'WorldParticleSystem', 'TraineeGameplayCoordinatorWorldComponent', 'EagleVisionManager'):
                    content += 1
                    rows.append((na[i], 'content (only AC3 here)', len(a[i][1]), 0))
                else:
                    mismatch += 1
                    rows.append((na[i], 'ONLY AC3', len(a[i][1]), 0))
            for j in range(j1, j2):
                if nb[j] in ('WorldParticleSystem', 'TraineeGameplayCoordinatorWorldComponent', 'EagleVisionManager'):
                    content += 1
                    rows.append((nb[j], 'content (only ours here)', 0, len(b[j][1])))
                else:
                    mismatch += 1
                    rows.append((nb[j], 'ONLY OURS', 0, len(b[j][1])))
    return (rows, (exact, content, mismatch))

def main(argv):
    pairs = pickle.load(open(os.path.join(common.OUT, 'pairs_worlds.pkl'), 'rb'))
    lines = []
    for m, (w3, w4, o3, o4) in pairs.items():
        lines.append(f'== {m}: AC3 World {len(w3)} B, AC4 {len(w4)} B')
        try:
            ours, grid, layers, log = convert_world(w4, keep_wps=True, keep_events=True, fakes=True)
        except WorldError as e:
            lines.append(f'   REFUSED: {e}')
            continue
        for ln in log:
            lines.append(f'   - {ln}')
        rows, (exact, content, mismatch) = compare(w3, ours)
        lines.append(f'   World body: {len(ours)} B ours vs {len(w3)} B AC3; components exact {exact} content {content} mismatch {mismatch}')
        for n, v, la, lb in rows:
            if v != 'exact':
                lines.append(f'      {n:44} {v:12} AC3 {la:5} ours {lb:5}')
        g3 = [o for r, o in o3.values() if common.cls(o['type'], 'AC3') == 'GridPartition']
        l3 = [o for r, o in o3.values() if common.cls(o['type'], 'AC3') == 'WorldDataLayerManager']
        if g3:
            same = g3[0]['body'] == grid
            lines.append(f"   GridPartition object: {('exact' if same else 'differs')} (AC3 {len(g3[0]['body'])} B, ours {len(grid)} B)")
            if not same and len(g3[0]['body']) == len(grid):
                n, pos = common.differing_bytes(g3[0]['body'], grid)
                lines.append(f'      {n} bytes differ at {pos[:12]}')
        if l3:
            same = l3[0]['body'] == layers
            n3, no = (u32(l3[0]['body'], 10), u32(layers, 10))
            lines.append(f"   WorldDataLayerManager object: {('exact' if same else 'differs')} (AC3 {n3} entries, ours {no})")
            if not same:
                e3 = {l3[0]['body'][14 + 18 * i:14 + 18 * (i + 1)] for i in range(n3)}
                eo = {layers[14 + 18 * i:14 + 18 * (i + 1)] for i in range(no)}
                lines.append(f'      entries only AC3 {len(e3 - eo)}, only ours {len(eo - e3)}, shared {len(e3 & eo)}')
                only_ours = sorted(eo - e3)
                lines.append('      only ours (layer uids): ' + ', '.join((f'{u32(e, 9):08X}' for e in only_ours[:24])))
    common.report('world_rules_gate.txt', lines)
    print('\n'.join(lines))
if __name__ == '__main__':
    main(sys.argv[1:])
