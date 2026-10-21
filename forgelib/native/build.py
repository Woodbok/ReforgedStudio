"""forgelib/native/build.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import glob
import os
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, 'ac3lzo.dll')

def vcvars64():
    pf86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
    vswhere = os.path.join(pf86, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    if os.path.exists(vswhere):
        r = subprocess.run([vswhere, '-latest', '-products', '*', '-property', 'installationPath'], capture_output=True, text=True)
        for root in r.stdout.split('\n'):
            p = os.path.join(root.strip(), 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
            if root.strip() and os.path.exists(p):
                return p
    hits = glob.glob(os.path.join(pf86, 'Microsoft Visual Studio', '*', '*', 'VC', 'Auxiliary', 'Build', 'vcvars64.bat'))
    if not hits:
        raise SystemExit('vcvars64.bat not found: install the Visual Studio Build Tools (Desktop development with C++)')
    return sorted(hits)[-1]

def main():
    obj = os.path.join(HERE, 'obj')
    os.makedirs(obj, exist_ok=True)
    src = os.path.join(HERE, 'ac3lzo.c')
    tmp = os.path.join(obj, 'ac3lzo.dll')
    cmd = f'call "{vcvars64()}" >nul && cl /nologo /O2 /MT /W4 /LD "{src}" /Fo"{obj}\\\\" /link /OUT:"{tmp}" /IMPLIB:"{obj}\\ac3lzo.lib"'
    r = subprocess.run(cmd, shell=True)
    if r.returncode:
        raise SystemExit(f'build failed ({r.returncode})')
    try:
        os.replace(tmp, DLL)
    except PermissionError:
        raise SystemExit(f'{DLL} is in use (a Python holds it); built {tmp}')
    print(DLL)
if __name__ == '__main__':
    sys.exit(main())
