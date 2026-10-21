"""forgelib/crossmaps/acb_calls.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import argparse
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'audio'), os.path.join(ROOT, 'maps')):
    if p not in sys.path:
        sys.path.insert(0, p)
OUT = os.path.join(ROOT, 'out', 'maps')
MERCHANT_CALLS = '0xF22C8CDB,0x413CF78A,0x62A558D9,0xE1949DC4,0x4B827153,0x65CDC705,0x4115FDD5'
ORATOR_SPEECHES = '0xACE7E5F8,0xD1ED3087'

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--prefix', required=True)
    ap.add_argument('--archive', default='DataPC_AC2MP_Forli.forge')
    ap.add_argument('--uid-base', required=True)
    ap.add_argument('--uid-limit', default='0x800')
    ap.add_argument('--stalls', default='AC2MP_GEN_AMerchant')
    ap.add_argument('--calls', help="the ACB events the stalls call, round robin (default: the merchants')")
    a = ap.parse_args(argv)
    import stall_calls
    build = f'{a.prefix}_' + a.src.split('_', 1)[1]
    rc = stall_calls.main([build, '--src', os.path.join(OUT, a.src, a.archive), '--prefix', a.stalls, '--uid-base', a.uid_base, '--uid-limit', a.uid_limit, '--acb', '--calls', a.calls or MERCHANT_CALLS])
    if rc:
        raise SystemExit(rc)
if __name__ == '__main__':
    main(sys.argv[1:])
