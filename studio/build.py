"""Builds Reforged Studio (studio/build/ReforgedStudio.exe) with the Visual Studio C++ Build Tools (x64).

    python studio/build.py            (--clean rebuilds the third-party code too)

Finds vcvars64.bat through vswhere. Dear ImGui (docking), its Win32 /
DirectX 11 backends and ImGuizmo are compiled once into build/obj/ and reused while their sources don't change.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
TP = os.path.join(HERE, "third_party")
OUT = os.path.join(HERE, "build")
OBJ = os.path.join(OUT, "obj")
IMGUI = os.path.join(TP, "imgui")
THIRD = [os.path.join(IMGUI, f) for f in ("imgui.cpp", "imgui_draw.cpp", "imgui_tables.cpp", "imgui_widgets.cpp",
                                          "backends/imgui_impl_win32.cpp", "backends/imgui_impl_dx11.cpp")] + \
        [os.path.join(TP, "ImGuizmo", "ImGuizmo.cpp")]
LIBS = "d3d11.lib dxgi.lib d3dcompiler.lib user32.lib gdi32.lib comdlg32.lib shell32.lib ole32.lib dwmapi.lib"


def vcvars64():
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = os.path.join(pf86, "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if os.path.exists(vswhere):
        r = subprocess.run([vswhere, "-latest", "-products", "*", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                           capture_output=True, text=True)
        for root in r.stdout.split("\n"):
            p = os.path.join(root.strip(), "VC", "Auxiliary", "Build", "vcvars64.bat")
            if root.strip() and os.path.exists(p):
                return p
    hits = glob.glob(os.path.join(pf86, "Microsoft Visual Studio", "*", "*", "VC", "Auxiliary", "Build", "vcvars64.bat"))
    if not hits:
        raise SystemExit("vcvars64.bat not found: install the Visual Studio Build Tools (Desktop development with C++)")
    return sorted(hits)[-1]


def obj_of(src):
    return os.path.join(OBJ, os.path.splitext(os.path.basename(src))[0] + ".obj")


def run(cmd):
    r = subprocess.run(f'call "{vcvars64()}" >nul && {cmd}', shell=True)
    if r.returncode:
        raise SystemExit(f"build failed ({r.returncode})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--debug", action="store_true", help="no optimisation, debug info")
    ap.add_argument("--exe", default="ReforgedStudio.exe", help="the program's file name (another name while the Studio is open)")
    a = ap.parse_args()
    if a.clean and os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OBJ, exist_ok=True)
    inc = f'/I"{SRC}" /I"{IMGUI}" /I"{os.path.join(TP, "ImGuizmo")}" /I"{os.path.join(TP, "nlohmann")}"'
    opt = "/Od /Zi /MTd" if a.debug else "/O2 /MT"
    flags = f"/nologo /std:c++17 /EHsc /utf-8 /W3 {opt} /DNOMINMAX /DUNICODE /D_UNICODE /D_CRT_SECURE_NO_WARNINGS {inc} /MP"
    stale = [s for s in THIRD if not os.path.exists(obj_of(s)) or os.path.getmtime(obj_of(s)) < os.path.getmtime(s)]
    if stale:
        print(f"compiling {len(stale)} third-party file(s)...")
        run(f'cl {flags} /c {" ".join(chr(34) + s + chr(34) for s in stale)} /Fo"{OBJ}\\\\"')
    own = sorted(glob.glob(os.path.join(SRC, "*.cpp")))
    print(f"compiling {len(own)} studio file(s)...")
    run(f'cl {flags} /c {" ".join(chr(34) + s + chr(34) for s in own)} /Fo"{OBJ}\\\\"')
    objs = " ".join(f'"{obj_of(s)}"' for s in THIRD + own)
    exe = os.path.join(OUT, a.exe)
    run(f'link /nologo {"/DEBUG" if a.debug else ""} /SUBSYSTEM:WINDOWS /OUT:"{exe}" {objs} {LIBS}')
    print(exe)


if __name__ == "__main__":
    sys.exit(main())
