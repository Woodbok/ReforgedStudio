"""forgelib/maps/port/build_map.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import shutil
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS = os.path.dirname(HERE)
ROOT = os.path.dirname(MAPS)
OUT = os.path.join(ROOT, 'out', 'maps')
sys.path.insert(0, HERE)
sys.path.insert(1, ROOT)
import configs
import paths

def run(key, step, args):
    cmd = [sys.executable, os.path.join(HERE, 'run_step.py'), '--port-map', key, step] + args
    r = subprocess.run(cmd, cwd=MAPS, capture_output=True, text=True)
    tail = [line for line in r.stdout.splitlines() if 'verify' in line or line.startswith(('wrote', 'port:', '->'))]
    print('\n'.join(('   ' + line[:300] for line in tail)), flush=True)
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit(f'step {step} failed')

def expand(step, cfg):
    out = []
    for s in step[1:]:
        if s == '{A}':
            out += ['--archive', cfg['archive']]
        elif s == '{M}':
            out += ['--map', cfg['archive']]
        else:
            out.append(s)
    return (step[0], out)

def chain(key, cfg, steps, src, name, made, last_is_final):
    for i, step in enumerate(steps):
        script, args = expand(step, cfg)
        prefix = name if last_is_final and i == len(steps) - 1 else f'{name}t{i:02d}'
        print(f"== {' '.join(step)}  ({src} -> {prefix}_{cfg['tag']})", flush=True)
        run(key, script.replace('port/', ''), ['--src', src, '--prefix', prefix] + args)
        src = f"{prefix}_{cfg['tag']}"
        made.append(src)
    return src

def stage0(key, cfg):
    if os.path.exists(os.path.join(OUT, cfg['source'], cfg['archive'] + '.stage0')):
        return
    print(f"== ac2mp_benches.py  (stage 0: out/maps/{cfg['source']} is missing)", flush=True)
    run(key, 'ac2mp_benches.py', ['--archive', cfg['archive'], '--prefix', cfg['source'].rsplit('_', 1)[0]])

def stage1(key, cfg, name, made):
    first = f"{name}p_{cfg['tag']}"
    print(f"== build_static.py  (ACR {cfg['map']} -> {first})", flush=True)
    run(key, 'build_static.py', list(cfg.get('BUILD_STATIC') or [cfg['map']]) + ['--name', first])
    made.append(first)
    return chain(key, cfg, cfg['STAGE1'], first, f'{name}p', made, False)

def menu(key, cfg, name, base):
    m = cfg['MENU']
    made = []
    src = os.path.abspath(base) if base and base != 'pristine' else None
    steps = [(m['label'], None, [])] + [(f"{m['label']}_{amb['mode']}", amb['hour'], [f"map_entry.PICTURES={{0x5FE98FB8: {amb['picture']}}}"]) for amb in m.get('ambiences', [])]
    for i, (label, hour, sets) in enumerate(steps):
        entry = f"{name}_{cfg['tag']}_entry{i}"
        print(f'== map_entry.py {label}' + (f' at {hour}' if hour is not None else '') + f"  ({src or 'pristine DataPC'} -> {entry})", flush=True)
        args = [a for s_ in sets for a in ('--set', s_)]
        args += ['map_entry.py', '--name', entry, '--label', label, '--name-id', m['name_id'], '--desc-id', m['desc_id']]
        args += (['--hour', str(hour)] if hour is not None else []) + ['--base', src or 'pristine']
        cmd = [sys.executable, os.path.join(HERE, 'run_step.py'), '--port-map', key] + args
        r = subprocess.run(cmd, cwd=MAPS, capture_output=True, text=True)
        print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines() if 'verify' in line or '(--set)' in line or 'start hour' in line or line.startswith('MpWorld_'))), flush=True)
        if r.returncode:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit('map_entry failed')
        src = os.path.join(OUT, entry, 'DataPC.forge')
        made.append(entry)
        given = next((int(x.split('unlock index ')[1].split(' -> ')[1].split(',')[0]) for x in r.stdout.splitlines() if x.startswith(f'Map_{label}:') and 'unlock index ' in x), None)
        if m.get('unlock') and given != m['unlock'][i]:
            index = m['unlock'][i]
            moved = f'{entry}u'
            print(f'== set_unlock.py Map_{label} -> {index}', flush=True)
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'crossmaps', 'set_unlock.py'), '--label', label, '--base', src, '--index', str(index), '--name', moved, '--owner', 'maps'], cwd=ROOT, capture_output=True, text=True)
            print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines() if line.startswith('Map_'))), flush=True)
            if r.returncode:
                print(r.stdout[-3000:], r.stderr[-3000:])
                raise SystemExit('set_unlock failed')
            src = os.path.join(OUT, moved, 'DataPC.forge')
            made.append(moved)
    dest = os.path.join(OUT, f"{name}_{cfg['tag']}_named")
    print(f"== strings/add_strings.py  ({made[-1]} -> {name}_{cfg['tag']}_named)", flush=True)
    cmd = [sys.executable, os.path.join(ROOT, 'strings', 'add_strings.py'), '--base', src, '--out', dest, *(['--from-acr-as', f"{m['name_acr']}={m['name_id']}"] if m.get('name_acr') else ['--from-acr', m['name_id']]), '--add', f"{m['desc_id']}={m['desc_text']}", '--owner', 'maps', '--no-register']
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines()[-4:])))
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit('add_strings failed')
    return made

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('map', choices=sorted(configs.MAPS))
    ap.add_argument('--name', help="the final build's prefix, e.g. K10 -> out/maps/K10_<tag>/")
    ap.add_argument('--src', help="start stage 2 from this build (default: stage 1 from ACR's archive first)")
    ap.add_argument('--menu', help='build the menu DataPC under this prefix')
    ap.add_argument('--base', default=paths.DEFAULT_DATAPC, help="with --menu: the DataPC.forge to add the entry to (default: the default set's, paths.DEFAULT_DATAPC - the current release, the Jester included; 'pristine' for the untouched game's)")
    ap.add_argument('--keep', action='store_true', help='keep the intermediate builds')
    ap.add_argument('--resume-at', metavar='TEXT', help='with --src: start stage 2 at the first step whose command line contains TEXT (the steps before it are already in --src, e.g. --src K15_const --resume-at merchant_stalls)')
    a = ap.parse_args(argv)
    if not a.name and (not a.menu):
        ap.error('--name and / or --menu')
    cfg = configs.MAPS[a.map]
    if a.menu:
        for entry in menu(a.map, cfg, a.menu, a.base):
            if not a.keep:
                shutil.rmtree(os.path.join(OUT, entry), ignore_errors=True)
    if a.name:
        made = []
        if not a.src and cfg.get('source'):
            stage0(a.map, cfg)
        src = a.src or stage1(a.map, cfg, a.name, made)
        steps = cfg['STAGE2']
        if not steps or steps[-1][0] != 'port/stock_twins.py':
            steps = steps + [['port/stock_twins.py', '{A}']]
        steps = steps + configs.mode_steps(a.map)
        steps = steps + [['port/own_filters.py', '{A}']]
        if a.resume_at:
            if not a.src:
                ap.error('--resume-at needs --src')
            at = next((i for i, st in enumerate(steps) if a.resume_at in ' '.join(st)), None)
            if at is None:
                ap.error(f'--resume-at {a.resume_at!r} matches no stage-2 step')
            print(f"== resumed at step {at}: {' '.join(steps[at])} (the {at} before it are in {a.src})", flush=True)
            steps = steps[at:]
        src_dir = os.path.join(OUT, src)
        if cfg.get('install_as') and (not os.path.exists(os.path.join(src_dir, cfg['archive']))):
            link = os.path.join(OUT, f"{a.name}src_{cfg['tag']}")
            os.makedirs(link, exist_ok=True)
            if not os.path.exists(os.path.join(link, cfg['archive'])):
                os.link(os.path.join(src_dir, cfg['install_as']), os.path.join(link, cfg['archive']))
            src = f"{a.name}src_{cfg['tag']}"
            made.append(src)
        chain(a.map, cfg, steps, src, a.name, made, True)
        if not a.keep:
            for d in made[:-1]:
                shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)
        checks(a.map, made[-1])
        if cfg.get('install_as'):
            os.replace(os.path.join(OUT, made[-1], cfg['archive']), os.path.join(OUT, made[-1], cfg['install_as']))
            print(f"renamed {cfg['archive']} -> {cfg['install_as']}")
        print(f'done: out/maps/{made[-1]}/')

def checks(key, build):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'checks.py'), key, build], cwd=HERE, capture_output=True, text=True)
    print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines())), flush=True)
    order = [line for line in r.stdout.splitlines() if 'before them (never built):' in line]
    if r.returncode or not order or (not order[0].split('before them (never built):')[1].strip().startswith('0 ')):
        print(r.stderr[-2000:])
        raise SystemExit('checks failed: check_order must report 0 before them')
if __name__ == '__main__':
    main(sys.argv[1:])
