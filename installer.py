#!/usr/bin/env python3
import os
import io
import json
import shutil
import hashlib
import zipfile
import tempfile
import argparse
import subprocess
import urllib.request
from pathlib import Path
import string


def main():
    # other architectures may work or may not - not really tested
    HOST = "x64"  # or x86
    TARGET = "x64"  # or x86, arm, arm64

    MANIFEST_URL = "https://aka.ms/vs/17/release/channel"

    def download(url):
        with urllib.request.urlopen(url) as res:
            return res.read()

    def download_progress(url, check, name, f):
        data = io.BytesIO()
        with urllib.request.urlopen(url) as res:
            total = int(res.headers["Content-Length"])
            size = 0
            while True:
                block = res.read(1 << 20)
                if not block:
                    break
                f.write(block)
                data.write(block)
                size += len(block)
                perc = size * 100 // total
                print(f"\r{name} ... {perc}%", end="")
        print()
        data = data.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if check.lower() != digest:
            print(f"Hash mismatch for f{pkg}")
            exit(1)
        return data

    # super crappy msi format parser just to find required .cab files
    def get_msi_cabs(msi):
        index = 0
        while True:
            index = msi.find(b".cab", index + 4)
            if index < 0:
                return
            yield msi[index - 32 : index + 4].decode("ascii")

    def first(items, cond):
        return next(item for item in items if cond(item))

    ### parse command-line arguments

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--show-versions",
        const=True,
        action="store_const",
        help="Show available MSVC and Windows SDK versions",
    )
    ap.add_argument(
        "--accept-license",
        const=True,
        action="store_const",
        help="Automatically accept license",
    )
    ap.add_argument("--msvc-version", help="Get specific MSVC version")
    ap.add_argument("--sdk-version", help="Get specific Windows SDK version")
    ap.add_argument("--output-dir", help="Specify output directory")
    ap.add_argument("--components", action="extend", nargs="+", type=str)
    ap.add_argument(
        "--discard", nargs="*", help="Extra arguments which will be discarded"
    )
    args = ap.parse_args()

    OUTPUT = Path(set(args.output_dir))  # output folder

    # get and validate components
    components = set(args.components)
    available_components = {"msvc", "asan", "sdk", "crt"}
    if not components.issubset(available_components):
        raise ValueError(f"Invalid components {components - available_components}")

    ### get main manifest
    manifest = json.loads(download(MANIFEST_URL))

    ### download VS manifest
    vs = first(
        manifest["channelItems"],
        lambda x: x["id"] == "Microsoft.VisualStudio.Manifests.VisualStudio",
    )
    payload = vs["payloads"][0]["url"]

    vsmanifest = json.loads(download(payload))

    ### find MSVC & WinSDK versions
    packages = {}
    for p in vsmanifest["packages"]:
        packages.setdefault(p["id"].lower(), []).append(p)

    msvc = {}
    sdk = {}

    for pid, p in packages.items():
        if pid.startswith(
            "Microsoft.VisualStudio.Component.VC.".lower()
        ) and pid.endswith(".x86.x64".lower()):
            pver = ".".join(pid.split(".")[4:6])
            if pver[0].isnumeric():
                msvc[pver] = pid
        elif pid.startswith(
            "Microsoft.VisualStudio.Component.Windows10SDK.".lower()
        ) or pid.startswith("Microsoft.VisualStudio.Component.Windows11SDK.".lower()):
            pver = pid.split(".")[-1]
            if pver.isnumeric():
                sdk[pver] = pid

    if args.show_versions:
        print("MSVC versions:", " ".join(sorted(msvc.keys())))
        print("Windows SDK versions:", " ".join(sorted(sdk.keys())))
        exit(0)

    msvc_ver = args.msvc_version or max(sorted(msvc.keys()))
    sdk_ver = args.sdk_version or max(sorted(sdk.keys()))

    if msvc_ver in msvc:
        msvc_pid = msvc[msvc_ver]
        msvc_ver = ".".join(msvc_pid.split(".")[4:-2])
    else:
        exit(f"Unknown MSVC version: {args.msvc_version}")

    if sdk_ver in sdk:
        sdk_pid = sdk[sdk_ver]
    else:
        exit(f"Unknown Windows SDK version: {args.sdk_version}")

    print(f"Downloading MSVC v{msvc_ver} and Windows SDK v{sdk_ver}")

    ### agree to license

    tools = first(
        manifest["channelItems"],
        lambda x: x["id"] == "Microsoft.VisualStudio.Product.BuildTools",
    )
    resource = first(tools["localizedResources"], lambda x: x["language"] == "en-us")
    license = resource["license"]

    if not args.accept_license:
        accept = input(f"Do you accept Visual Studio license at {license} [Y/N] ? ")
        if not accept or accept[0].lower() != "y":
            exit(0)

    OUTPUT.mkdir(exist_ok=True, parents=True)
    total_download = 0

    msvcv = None
    sdkv = None

    ### download MSVC
    msvc_packages = {
        "msvc": [
            # MSVC binaries
            f"microsoft.vc.{msvc_ver}.tools.host{HOST}.target{TARGET}.base",
            f"microsoft.vc.{msvc_ver}.tools.host{HOST}.target{TARGET}.res.base",
        ],
        "crt": [
            # MSVC headers
            f"microsoft.vc.{msvc_ver}.crt.headers.base",
            # MSVC libs
            f"microsoft.vc.{msvc_ver}.crt.{TARGET}.desktop.base",
            f"microsoft.vc.{msvc_ver}.crt.{TARGET}.store.base",
            # MSVC runtime source
            f"microsoft.vc.{msvc_ver}.crt.source.base",
        ],
        "asan": [
            # ASAN
            f"microsoft.vc.{msvc_ver}.asan.headers.base",
            f"microsoft.vc.{msvc_ver}.asan.{TARGET}.base",
            # MSVC redist
            # f"microsoft.vc.{msvc_ver}.crt.redist.x64.base",
        ],
    }
    selected_msvc_components = [
        c for component in components for c in msvc_packages.get(component, [])
    ]
    for pkg in selected_msvc_components:
        p = first(packages[pkg], lambda p: p.get("language") in (None, "en-US"))
        for payload in p["payloads"]:
            with tempfile.TemporaryFile() as f:
                data = download_progress(payload["url"], payload["sha256"], pkg, f)
                total_download += len(data)
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.startswith("Contents/"):
                            out = OUTPUT / Path(name).relative_to("Contents")
                            out.parent.mkdir(parents=True, exist_ok=True)
                            out.write_bytes(z.read(name))

    msvc_install_dir = list((OUTPUT / "VC/Tools/MSVC").glob("*"))
    if msvc_install_dir:
        msvcv = msvc_install_dir[0].name

    ### download Windows SDK

    if "sdk" in components:
        sdk_packages = [
            # Windows SDK tools (like rc.exe & mt.exe)
            f"Windows SDK for Windows Store Apps Tools-x86_en-us.msi",
            # Windows SDK headers
            f"Windows SDK for Windows Store Apps Headers-x86_en-us.msi",
            f"Windows SDK Desktop Headers x86-x86_en-us.msi",
            # Windows SDK libs
            f"Windows SDK for Windows Store Apps Libs-x86_en-us.msi",
            f"Windows SDK Desktop Libs {TARGET}-x86_en-us.msi",
            # CRT headers & libs
            f"Universal CRT Headers Libraries and Sources-x86_en-us.msi",
            # CRT redist
            # "Universal CRT Redistributable-x86_en-us.msi",
        ]

        with tempfile.TemporaryDirectory() as d:
            dst = Path(d)

            sdk_pkg = packages[sdk_pid][0]
            sdk_pkg = packages[first(sdk_pkg["dependencies"], lambda x: True).lower()][
                0
            ]

            msi = []
            cabs = []

            # download msi files
            for pkg in sdk_packages:
                payload = first(
                    sdk_pkg["payloads"], lambda p: p["fileName"] == f"Installers\\{pkg}"
                )
                msi.append(dst / pkg)
                with open(dst / pkg, "wb") as f:
                    data = download_progress(payload["url"], payload["sha256"], pkg, f)
                total_download += len(data)
                cabs += list(get_msi_cabs(data))

            # download .cab files
            for pkg in cabs:
                payload = first(
                    sdk_pkg["payloads"], lambda p: p["fileName"] == f"Installers\\{pkg}"
                )
                with open(dst / pkg, "wb") as f:
                    download_progress(payload["url"], payload["sha256"], pkg, f)

            print("Unpacking msi files...")

            # run msi installers
            for m in msi:
                subprocess.check_call(
                    [
                        "msiexec.exe",
                        "/a",
                        m,
                        "/quiet",
                        "/qn",
                        f"TARGETDIR={OUTPUT.resolve()}",
                    ]
                )

            sdkv = list((OUTPUT / "Windows Kits/10/bin").glob("*"))[0].name

    ### versions

    # place debug CRT runtime files into MSVC folder (not what real Visual Studio installer does... but is reasonable)
    if msvcv:
        dst = OUTPUT / "VC/Tools/MSVC" / msvcv / f"bin/Host{HOST}/{TARGET}"

    # with tempfile.TemporaryDirectory() as d:
    #   d = Path(d)
    #   pkg = "microsoft.visualcpp.runtimedebug.14"
    #   dbg = first(packages[pkg], lambda p: p["chip"] == HOST)
    #   for payload in dbg["payloads"]:
    #     name = payload["fileName"]
    #     with open(d / name, "wb") as f:
    #       data = download_progress(payload["url"], payload["sha256"], f"{pkg}/{name}", f)
    #       total_download += len(data)
    #   msi = d / first(dbg["payloads"], lambda p: p["fileName"].endswith(".msi"))["fileName"]

    #   with tempfile.TemporaryDirectory() as d2:
    #     subprocess.check_call(["msiexec.exe", "/a", str(msi), "/quiet", "/qn", f"TARGETDIR={d2}"])
    #     for f in first(Path(d2).glob("System*"), lambda x: True).iterdir():
    #       f.replace(dst / f.name)

    # # download DIA SDK and put msdia140.dll file into MSVC folder

    # with tempfile.TemporaryDirectory() as d:
    #   d = Path(d)
    #   pkg = "microsoft.visualc.140.dia.sdk.msi"
    #   dia = packages[pkg][0]
    #   for payload in dia["payloads"]:
    #     name = payload["fileName"]
    #     with open(d / name, "wb") as f:
    #       data = download_progress(payload["url"], payload["sha256"], f"{pkg}/{name}", f)
    #       total_download += len(data)
    #   msi = d / first(dia["payloads"], lambda p: p["fileName"].endswith(".msi"))["fileName"]

    #   with tempfile.TemporaryDirectory() as d2:
    #     subprocess.check_call(["msiexec.exe", "/a", str(msi), "/quiet", "/qn", f"TARGETDIR={d2}"])

    #     if HOST == "x86": msdia = "msdia140.dll"
    #     elif HOST == "x64": msdia = "amd64/msdia140.dll"
    #     else: exit("unknown")

    #     src = Path(d2) / "Program Files" / "Microsoft Visual Studio 14.0" / "DIA SDK" / "bin" / msdia
    #     src.replace(dst / "msdia140.dll")

    ### cleanup
    shutil.rmtree(OUTPUT / "Common7", ignore_errors=True)
    if msvcv:
        for f in ["Auxiliary", f"lib/{TARGET}/store", f"lib/{TARGET}/uwp"]:
            shutil.rmtree(OUTPUT / "VC/Tools/MSVC" / msvcv / f, ignore_errors=True)
    for f in OUTPUT.glob("*.msi"):
        f.unlink()
    if sdkv:
        for f in [
            "Catalogs",
            "DesignTime",
            f"bin/{sdkv}/chpe",
            f"Lib/{sdkv}/ucrt_enclave",
        ]:
            shutil.rmtree(OUTPUT / "Windows Kits/10" / f, ignore_errors=True)
    for arch in ["x86", "x64", "arm", "arm64"]:
        if arch != TARGET:
            if msvcv:
                shutil.rmtree(
                    OUTPUT / "VC/Tools/MSVC" / msvcv / f"bin/Host{arch}",
                    ignore_errors=True,
                )
            if sdkv:
                shutil.rmtree(
                    OUTPUT / "Windows Kits/10/bin" / sdkv / arch, ignore_errors=True
                )
                shutil.rmtree(
                    OUTPUT / "Windows Kits/10/Lib" / sdkv / "ucrt" / arch,
                    ignore_errors=True,
                )
                shutil.rmtree(
                    OUTPUT / "Windows Kits/10/Lib" / sdkv / "um" / arch,
                    ignore_errors=True,
                )

    class Environment:
        # Read in conda build environment variables
        # This allows us to pass them round and refer to them as
        # e.recipe_dir, etc.
        items = [
            "RECIPE_DIR",
            "SRC_DIR",
            "PREFIX",
            "LIBRARY_PREFIX",
            "LIBRARY_BIN",
        ]

        def __init__(self):
            self.__attrs = {}
            for i in self.items:
                key = i.lower()
                value = os.environ.get(i, None)
                if value is None:
                    if i == "LIBRARY_BIN":
                        value = os.path.join(os.environ.get("PREFIX"), "Library", "bin")
                    else:
                        raise RuntimeError(f"{i} not set in environment")
                self.__attrs[key] = Path(value)
                print(f"{key}: {value}")

        def __getattr__(self, name):
            if name in self.__attrs:
                return self.__attrs[name]
            else:
                raise AttributeError

    ### setup.bat
    class AtTemplate(string.Template):
        delimiter = "@"

    def subs(line, args):
        t = AtTemplate(line)
        d = {
            "PREFIX": OUTPUT,
            "MSVC_VERSION": msvcv,
            "HOST_ARCH": HOST,
            "TARGET_ARCH": TARGET,
            "SDK_VERSION": sdkv,
            "SDK_TARGET_ARCH": TARGET,
        }
        return t.substitute(d)

    env = Environment()
    activation_hooks_dir = Path(env.prefix) / "etc" / "conda" / "activate.d"
    deactivation_hooks_dir = Path(env.prefix) / "etc" / "conda" / "deactivate.d"
    os.makedirs(activation_hooks_dir, exist_ok=True)
    os.makedirs(deactivation_hooks_dir, exist_ok=True)

    def copy_and_rename(source, target):
        with open(source, "r") as r:
            with open(target, "w") as w:
                for line in r:
                    w.write(subs(line, args))

    if "msvc" in components:
        copy_and_rename(
            env.recipe_dir / "activate_msvc.bat",
            activation_hooks_dir / "vs_buildtools-msvc.bat",
        )
        copy_and_rename(
            env.recipe_dir / "deactivate_msvc.bat",
            deactivation_hooks_dir / "vs_buildtools-msvc.bat",
        )

    if "sdk" in components:
        copy_and_rename(
            env.recipe_dir / "activate_sdk.bat",
            activation_hooks_dir / "vs_buildtools-sdk.bat",
        )
        copy_and_rename(
            env.recipe_dir / "deactivate_sdk.bat",
            deactivation_hooks_dir / "vs_buildtools-sdk.bat",
        )

    print(f"Total downloaded: {total_download>>20} MB")


if __name__ == "__main__":
    main()
