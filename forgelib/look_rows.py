"""forgelib/look_rows.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import re
import struct
import clone
import exe_registry
import persona
import tables
from datafile import decompress
from forge import Forge
from transplant import build_object
PART_TABLES = {107: 'MainPartTable', 112: 'SecondaryPartTable', 117: 'HeadTable', 122: 'BeltTable', 127: 'LegsTable'}
COSTUMES_AT, WINTER_AT = (147, 152)
COSTUME_TABLE = 'Costumes_Temp'
COSTUME_ROW_FROM_END = 23
PREFER = {33: ('MainPartTable', 'SecondaryPartTable', 'HeadTable', 'LegsTable', 'BeltTable'), 38: ('HeadTable', 'MainPartTable', 'SecondaryPartTable', 'LegsTable', 'BeltTable'), 43: ('HeadTable', 'MainPartTable', 'SecondaryPartTable', 'LegsTable', 'BeltTable')}

def _rows(plain):
    body = clone.objects_of(plain)[0]['body']
    return [(k, [(col, cls, ref) for _p, col, cls, ref in cells]) for k, (_s, _c, cells) in enumerate(tables.rows_of(body))]

def _filled(cells):
    return any((ref for _c, _k, ref in cells))
SLOT_TABLES = {0: 'MainPartTable', 1: 'SecondaryPartTable', 2: 'HeadTable', 3: 'BeltTable', 4: 'LegsTable', 6: COSTUME_TABLE}

def selected_rows(objects, persona_id):
    out = {t: {0} for t in SLOT_TABLES.values()}
    for o in objects:
        b = o['body']
        if o['name'].startswith(f'Char{persona_id}_') and len(b) > 27 and (b[len(b) - 27] in SLOT_TABLES):
            out[SLOT_TABLES[b[len(b) - 27]]].add(persona.u32(b, len(b) - COSTUME_ROW_FROM_END))
    return out

def spread_targets(c, names, sources, targets, log):
    import port_character as pc
    classes, _ = exe_registry.load(exe_registry.AC3)
    graphic = {i for i, n in classes.items() if n == 'GraphicObject'}
    objs = {n: clone.objects_of(sources[n]) for n in names if n in sources}
    roots = {o[0]['uid']: n for n, o in objs.items() if o and (not n.endswith('Table')) and (classes.get(o[0]['type']) != 'Skeleton')}
    defined = {n: {o['uid'] for o in ob} for n, ob in objs.items()}
    adds = {}

    def has(n, u):
        return u in defined[n] or u in adds.get(n, ())

    def need(rows, u):
        out = []
        for k, cells in rows:
            files = [roots[ref] for _c, _k, ref in cells if ref in roots]
            if not _filled(cells) or any((has(n, u) for n in files)):
                continue
            pick = [roots[ref] for _c, cls, ref in cells if ref in roots and cls in graphic] + files
            out.append((k, next((n for n in pick if n in adds), pick[0] if pick else None)))
        return out
    tabs = {t: _rows(sources[pc.donor_name(c, t)]) for t in list(PART_TABLES.values()) + [COSTUME_TABLE] if pc.donor_name(c, t) in sources}
    for off, u in sorted(targets.items()):
        if not u:
            continue
        src = next((o for n in names if n in objs for o in objs[n] if o['uid'] == u), None)
        if src is None:
            continue
        plans = [(t, need(tabs[t], u)) for t in PREFER[off] if t in tabs]
        if plans and (not any((not n for _t, n in plans))):
            _x, _l, _i, t, todo = min(((sum((1 for _k, f in n if not f)), len(n), PREFER[off].index(t), t, n) for t, n in plans))
            for _k, f in todo:
                if f and (not has(f, u)):
                    adds.setdefault(f, []).append(u)
            log(f'composite target +{off} 0x{u:08X}: into {t} rows {[k for k, f in todo if f]} ({sorted({f for _k, f in todo if f})})' + (f'; rows {[k for k, f in todo if not f]} name no copy of his' if any((not f for _k, f in todo)) else ''))
        if COSTUME_TABLE in tabs:
            todo = need([r for r in tabs[COSTUME_TABLE] if r[0] > 0], u)
            for _k, f in todo:
                if f and (not has(f, u)):
                    adds.setdefault(f, []).append(u)
            if todo:
                log(f'composite target +{off} 0x{u:08X}: into {COSTUME_TABLE} rows {[k for k, f in todo if f]}' + (f' (rows {[k for k, f in todo if not f]} name no copy of his)' if any((not f for _k, f in todo)) else ''))
    by_uid = {o['uid']: o for ob in objs.values() for o in ob if o['uid'] in set(targets.values())}
    for n, us in adds.items():
        new = [by_uid[u] for u in dict.fromkeys(us) if u not in defined[n]]
        if new:
            sources[n] = pc.rebuild(sources[n], additions=[build_object(o['type'], o['body_size'], o['name'], o['body']) for o in new])

def empty_unselected_costumes(c, names, sources, base_objects, base, added, log):
    import characters
    import port_character as pc
    key = pc.donor_name(c, COSTUME_TABLE)
    if key not in sources:
        return
    cut = characters.cut_rx(c)
    if any((o['name'].startswith(f'Char{c.new}_Costume_') for o in base_objects)):
        items = [o for o in base_objects if o['name'].startswith(f'Char{c.new}_Costume_')]
    else:
        items = [o for o in base_objects if o['name'].startswith(f'Char{c.donor}_Costume_') and (not (cut and re.fullmatch(cut, o['name'])))]
    keep = {persona.u32(o['body'], len(o['body']) - COSTUME_ROW_FROM_END) for o in items if len(o['body']) > COSTUME_ROW_FROM_END}
    with Forge(base) as f:
        rec = next((r for r in f.records if r.name == key), None)
        donor_rows = len(_rows(decompress(f.read(rec)))) if rec else 0
    mine = {clone.objects_of(sources[n])[0]['uid'] for n in added if n in sources}
    classes, _ = exe_registry.load(exe_registry.AC3)
    rows = _rows(sources[key])
    drop = {k for k, cells in rows if _filled(cells) and k not in keep and (k < donor_rows) and (not any((ref in mine and classes.get(cls) != 'Skeleton' for _c, cls, ref in cells)))}
    if not drop:
        return
    obj = pc.one_object(sources[key])
    body, removed = tables.remove_cells(obj['body'], lambda k, cell: True, rows=drop)
    sources[key] = pc.with_body(sources[key], obj, body)
    log(f"cut: {COSTUME_TABLE} rows {sorted(drop)} emptied ({len(removed)} cells: the donor's costumes no item of his selects; his rows {sorted(keep)})")

class BuildView:

    def __init__(self, dest):
        import forge
        self.f = Forge(dest)
        by_index = {r.index: r for r in self.f.records}
        self.by_key, self.by_name = ({}, {})
        for r in self.f.records:
            self.by_name.setdefault(r.name, r)
        fh, desc = (self.f._fh, self.f.index_offset + forge.DESCRIPTOR_OFFSET)
        while desc not in (forge.NONE64, 0):
            fh.seek(desc)
            d = fh.read(forge.DESCRIPTOR_SIZE)
            count, table, nxt = (struct.unpack_from('<I', d, 0)[0], struct.unpack_from('<Q', d, 8)[0], struct.unpack_from('<Q', d, 16)[0])
            first = struct.unpack_from('<I', d, 24)[0]
            fh.seek(table)
            e = fh.read(count * forge.ENTRY_SIZE)
            for i in range(count):
                self.by_key.setdefault(struct.unpack_from('<I', e, i * forge.ENTRY_SIZE + 8)[0], by_index[first + i])
            desc = nxt
        self._objs = {}

    def close(self):
        self.f.close()

    def objects(self, name):
        if name not in self._objs:
            try:
                self._objs[name] = clone.objects_of(decompress(self.f.read(self.by_name[name])))
            except Exception:
                self._objs[name] = []
        return self._objs[name]

    def name_of(self, key):
        r = self.by_key.get(key)
        return r.name if r else None

    def defines(self, name, uid):
        return any((o['uid'] == uid for o in self.objects(name)))

    def table(self, key):
        n = self.name_of(key)
        if n is None:
            return (None, None)
        try:
            return (n, _rows(decompress(self.f.read(self.by_name[n]))))
        except Exception:
            return (n, None)

def target_problems(view, kit_body, new_id, costume_adds=False, selected=None):
    out = []
    parts = {t: view.table(persona.u32(kit_body, off)) for off, t in PART_TABLES.items()}
    costumes = view.table(persona.u32(kit_body, COSTUMES_AT))

    def lacking(rows, u, table):
        return [k for k, cells in rows if _filled(cells) and (selected is None or k in selected[table]) and (not any((view.name_of(ref) and view.defines(view.name_of(ref), u) for _c, _k, ref in cells if ref)))]
    for off, u in persona.composite_targets(kit_body).items():
        if not u:
            continue
        what = f"kit {new_id}'s {persona.COMPOSITE_TARGETS[off]} (+{off}) 0x{u:08X}"
        per = {t: lacking(rows, u, t) for t, (_n, rows) in parts.items() if rows}
        if not any((not v for v in per.values())):
            out.append(f"{what} is in no part table's every look (" + '; '.join((f'{t} lacks it in rows {v}' for t, v in per.items() if len(v) < len(parts[t][1]))) + '): a look without it never resolves the handle (JT1 family; look_rows.spread_targets)')
        if costumes[1] and (not costume_adds):
            miss = [k for k in lacking(costumes[1], u, COSTUME_TABLE) if k > 0]
            if miss:
                out.append(f'{what} is in no file of {costumes[0]} rows {miss} (a costume replaces the look: FUN_0065F849; look_rows.spread_targets)')
    return out

def costume_problems(view, kit_body, objects, new_id):
    name, rows = view.table(persona.u32(kit_body, COSTUMES_AT))
    if not rows:
        return []
    sel = selected_rows(objects, new_id)[COSTUME_TABLE]
    bad = [k for k, cells in rows if _filled(cells) and k not in sel]
    return [f"{name} rows {bad} hold cells but none of his costume items selects them (rows {sorted(sel)}): the donor's costumes left (look_rows.empty_unselected_costumes)"] if bad else []

def check(dest, new_id, objects=None):
    from bootstrap_build import Bootstrap
    if objects is None:
        objects = Bootstrap(dest).objects
    kit = next((o['body'] for o in objects if o['name'] == f'CharacterBuildingKit_AC3MP_ID{new_id}'))
    exe = os.path.join(os.path.dirname(dest), 'AC3MP.exe')
    adds = False
    if os.path.exists(exe):
        import exe_costume
        from exe_patch import PE
        adds = new_id in exe_costume.flagged(PE(open(exe, 'rb').read()))
    view = BuildView(dest)
    try:
        return target_problems(view, kit, new_id, adds, selected_rows(objects, new_id)) + costume_problems(view, kit, objects, new_id)
    finally:
        view.close()
if __name__ == '__main__':
    import sys
    for p in check(os.path.join(sys.argv[1], 'DataPC.forge') if os.path.isdir(sys.argv[1]) else sys.argv[1], int(sys.argv[2])):
        print('PROBLEM', p)
