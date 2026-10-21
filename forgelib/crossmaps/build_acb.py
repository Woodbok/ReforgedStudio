"""forgelib/crossmaps/build_acb.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import acb
acb.patch()
import build_map

def menu(key, name, base=None, keep=False):
    import shutil
    import subprocess
    import acb_configs
    cfg = acb_configs.MAPS[key]
    m = cfg['MENU']
    src = os.path.abspath(base) if base else None
    made = []
    steps = [(m['label'], None, [], m['unlock'])] + [(f"{m['label']}_{amb['mode']}", amb['hour'], [f"map_entry.PICTURES={{0x5FE98FB8: {amb['picture']}}}"], amb['unlock']) for amb in m.get('ambiences', [])]
    for i, (label, hour, sets, unlock) in enumerate(steps):
        entry = f"{name}_{cfg['tag']}_entry{i}"
        args = [a for s_ in sets for a in ('--set', s_)]
        args += ['map_entry.py', '--name', entry, '--label', label, '--name-id', m['name_id'], '--desc-id', m['desc_id']]
        args += (['--hour', str(hour)] if hour is not None else []) + (['--base', src] if src else [])
        print(f'== map_entry.py {label}' + (f' at {hour}' if hour is not None else ''), flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'run_step.py'), '--port-map', key] + args, cwd=acb.MAPS, capture_output=True, text=True)
        print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines() if 'verify' in line or 'unlock' in line or line.startswith(('MpWorld_', 'Map_')))), flush=True)
        if r.returncode:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit('map_entry failed')
        made.append(entry)
        print(f'== set_unlock.py Map_{label} -> {unlock}', flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'set_unlock.py'), '--label', label, '--base', os.path.join(acb.ROOT, 'out', 'maps', entry, 'DataPC.forge'), '--index', str(unlock), '--name', entry + 'u'], cwd=acb.ROOT, capture_output=True, text=True)
        print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines() if line.startswith('Map_'))), flush=True)
        if r.returncode:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit('set_unlock failed')
        entry += 'u'
        made.append(entry)
        src = os.path.join(acb.ROOT, 'out', 'maps', entry, 'DataPC.forge')
    dest = os.path.join(acb.ROOT, 'out', 'maps', f"{name}_{cfg['tag']}_named")
    print(f'== add_strings_acb.py -> {dest}', flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, 'add_strings_acb.py'), '--base', src, '--out', dest, '--owner', 'ac4-backport', '--what', f"{m['label']} (ACB's name / description)", '--from-acr-as', f"{m['name_acb']}={m['name_id']}", '--from-acr-as', f"{m['desc_acb']}={m['desc_id']}"], cwd=acb.ROOT, capture_output=True, text=True)
    print('\n'.join(('   ' + line[:300] for line in r.stdout.splitlines()[-5:])))
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit('add_strings_acb failed')
    if not keep:
        for d in made:
            shutil.rmtree(os.path.join(acb.ROOT, 'out', 'maps', d), ignore_errors=True)
    print(f'done: {dest}')

def main(argv):
    if '--menu' in argv:
        i = argv.index('--menu')
        base = argv[argv.index('--base') + 1] if '--base' in argv else None
        return menu(argv[0], argv[i + 1], base, '--keep' in argv)
    if '--stage0' in argv:
        import acb_stage0
        i = argv.index('--stage0')
        acb_stage0.main([argv[0], '--name', argv[i + 1]])
        argv = argv[:i] + argv[i + 2:]
        if len(argv) == 1:
            return
    build_map.HERE = HERE
    build_map.main(argv)
if __name__ == '__main__':
    main(sys.argv[1:])
