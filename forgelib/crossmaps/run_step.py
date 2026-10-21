"""forgelib/crossmaps/run_step.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acb
acb.patch()
import run_step
if __name__ == '__main__':
    run_step.main(sys.argv[1:])
