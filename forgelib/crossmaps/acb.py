"""forgelib/crossmaps/acb.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAPS = os.path.join(ROOT, 'maps')
PORT = os.path.join(MAPS, 'port')
for p in (HERE, PORT, MAPS, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import paths
ACB = paths.install('ACB')['mp_dir']
ACB_MAPS = ('DataPC_AC2MP_Forli.forge', 'DataPC_AC2MP_Villa.forge', 'DataPC_AC2MP_Pienza_dlc.forge', 'DataPC_AC2MP_Alhambra_dlc.forge')
ALIASES = {'ACB_DataPC_extra.forge': 'DataPC_extra.forge'}
_done = []

def stage0_name(archive):
    return archive + '.stage0'

def source(archive):
    import acb_configs
    for cfg in acb_configs.MAPS.values():
        if cfg['archive'] == archive and cfg.get('stage0_src'):
            path = os.path.join(ROOT, 'out', 'crossmaps', cfg['stage0_src'], stage0_name(archive))
            if not os.path.exists(path):
                raise SystemExit(f'{archive}: stage-0 build {path} missing (acb_stage0.py)')
            return path
    return os.path.join(ACB, archive)

def built(archive):
    try:
        source(archive)
        return True
    except SystemExit:
        return False

def drop_stale_caches():
    for archive in ACB_MAPS:
        if not built(archive):
            continue
        src = source(archive)
        for cache in (os.path.join(ROOT, 'out', 'maps', 'inv', f'ACR_{archive}.pkl'), os.path.join(ROOT, 'out', 'maps', 'xrefs', f'ACR_{archive}.pkl')):
            if os.path.exists(cache) and os.path.getmtime(cache) < os.path.getmtime(src):
                os.remove(cache)

def patch():
    if _done:
        return
    import mapwalk
    orig_path, orig_list = (mapwalk.archive_path, mapwalk.map_archives)

    def archive_path(game, archive):
        if game == 'ACR' and os.path.basename(archive) in ACB_MAPS:
            return source(os.path.basename(archive))
        if game == 'ACR' and os.path.basename(archive) in ALIASES:
            return os.path.join(ACB, ALIASES[os.path.basename(archive)])
        return orig_path(game, archive)

    def map_archives(game):
        out = orig_list(game)
        return sorted(set(out) | {a for a in ACB_MAPS if built(a)}) if game == 'ACR' else out
    mapwalk.archive_path, mapwalk.map_archives = (archive_path, map_archives)
    drop_stale_caches()
    import configs as port_configs
    import acb_configs
    port_configs.MAPS.update(acb_configs.MAPS)
    import acb_blend
    acb_blend.patch()
    import acb_merchants
    acb_merchants.patch()
    import acb_trees
    acb_trees.patch()
    _done.append(True)
