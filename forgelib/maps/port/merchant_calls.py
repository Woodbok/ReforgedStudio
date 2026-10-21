"""forgelib/maps/port/merchant_calls.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, os.path.join(ROOT, 'audio'), os.path.join(ROOT, 'maps')):
    if p not in sys.path:
        sys.path.insert(0, p)
OUT = os.path.join(ROOT, 'out', 'maps')

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', default='DataPC_ACFE_Souk.forge')
    ap.add_argument('--uid-base', required=True)
    ap.add_argument('--uid-limit', default='0x800')
    ap.add_argument('--stalls', default='ACFE_GEN_Merchant')
    ap.add_argument('--calls', help="stall_calls.py --calls (default: the merchants')")
    a = ap.parse_args(argv)
    import stall_calls
    build = f'{a.prefix}_' + a.src.split('_', 1)[1]
    rc = stall_calls.main([build, '--src', os.path.join(OUT, a.src, a.archive), '--prefix', a.stalls, '--uid-base', a.uid_base, '--uid-limit', a.uid_limit] + (['--calls', a.calls] if a.calls else []))
    if rc:
        raise SystemExit(rc)
if __name__ == '__main__':
    main(sys.argv[1:])
