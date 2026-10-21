"""forgelib/maps/port/run_step.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import importlib
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
for p in (HERE, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import forge_write
import mapwalk
from forge import Forge
_dropped = []

def _tolerant_set_payload(orig):

    def set_payload(image, index, data):
        pos = image.order.index(index)
        if pos < len(image.gaps) and any(image.gaps[pos]):
            _dropped.append((index, len(image.gaps[pos])))
            image.gaps[pos] = bytes(len(image.gaps[pos]))
        return orig(image, index, data)
    return set_payload

def _skip_empty(orig):

    def payloads(game, archive, skip_errors=False):
        with Forge(mapwalk.archive_path(game, archive)) as f:
            empty = {r.index for r in f.records if not r.size and (not r.name)}
        if not empty:
            yield from orig(game, archive, skip_errors)
            return
        _skipped.append((archive, sorted(empty)))
        with Forge(mapwalk.archive_path(game, archive)) as f:
            for rec in f.records:
                if rec.name == 'GlobalMetaFile' or rec.index in empty:
                    continue
                try:
                    yield mapwalk.Payload(rec, f.read(rec))
                except Exception as e:
                    if not skip_errors:
                        raise mapwalk.MapWalkError(f'{archive} #{rec.index} {rec.name}: {e}') from e
    return payloads
_skipped = []

def apply(map_key, step_module, sets=()):
    forge_write.set_payload = _tolerant_set_payload(forge_write.set_payload)
    mapwalk.payloads = _skip_empty(mapwalk.payloads)
    import world
    import world_list
    world.convert_world = world_list.convert_world
    import configs
    cfg = configs.MAPS[map_key]
    lines = []
    if cfg.get('source') and step_module != 'ac2mp_benches':
        src = os.path.join(ROOT, 'out', 'maps', cfg['source'], cfg['archive'] + '.stage0')
        if not os.path.exists(src):
            raise SystemExit(f"{cfg['archive']}: stage-0 build {src} missing")
        orig_path = mapwalk.archive_path

        def archive_path(game, archive, _orig=orig_path, _src=src, _name=cfg['archive']):
            return _src if game == 'ACR' and os.path.basename(archive) == _name else _orig(game, archive)
        mapwalk.archive_path = archive_path
        for cache in (os.path.join(ROOT, 'out', 'maps', 'inv', f"ACR_{cfg['archive']}.pkl"), os.path.join(ROOT, 'out', 'maps', 'xrefs', f"ACR_{cfg['archive']}.pkl")):
            if os.path.exists(cache) and os.path.getmtime(cache) < os.path.getmtime(src):
                os.remove(cache)
        lines.append(f"ACR's {cfg['archive']} -> {os.path.relpath(src, ROOT)} (config source)")
    for (mod_name, attr), value in cfg.get('OVERRIDES', {}).items():
        mod = importlib.import_module(mod_name)
        old = getattr(mod, attr)
        setattr(mod, attr, value)
        lines.append(f'{mod_name}.{attr}: {old!r} -> {value!r}'[:300])
    import ast
    for item in sets:
        target, value = item.split('=', 1)
        mod_name, attr = target.rsplit('.', 1)
        mod = importlib.import_module(mod_name)
        old = getattr(mod, attr)
        setattr(mod, attr, ast.literal_eval(value))
        lines.append(f'{mod_name}.{attr}: {old!r} -> {getattr(mod, attr)!r} (--set)'[:300])
    return lines

def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--port-map', dest='map', required=True)
    ap.add_argument('--set', action='append', default=[], metavar='MODULE.CONST=VALUE', help="one more override for this call only (a Python literal), after the map's OVERRIDES")
    ap.add_argument('step')
    a, rest = ap.parse_known_args(argv)
    name = a.step[:-3] if a.step.endswith('.py') else a.step
    name = os.path.basename(name)
    os.chdir(MAPS)
    d = os.path.dirname(a.step)
    if d and os.path.normpath(os.path.join(MAPS, d)) not in sys.path:
        sys.path.insert(0, os.path.normpath(os.path.join(MAPS, d)))
    lines = apply(a.map, name, a.set)
    mod = importlib.import_module(name)
    for line in lines:
        print(f'port: {line}', flush=True)
    try:
        mod.main(rest)
    finally:
        if _dropped:
            print(f'port: {len(_dropped)} orphaned FILEDATA gap(s) dropped after rewritten files ({sum((n for _i, n in _dropped)):,} bytes of dead slots)', flush=True)
        for archive, idx in dict(_skipped).items():
            print(f"port: {archive}: empty nameless index entr{('y' if len(idx) == 1 else 'ies')} {idx} skipped by mapwalk.payloads", flush=True)
if __name__ == '__main__':
    main(sys.argv[1:])
