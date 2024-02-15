#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import os
import shutil
import string
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path


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
        exit(f"Hash mismatch for package {name}")
    return data


class AtTemplate(string.Template):
    delimiter = "@"


class Environment:
    items = [
        "RECIPE_DIR",
        "PREFIX",
        "LIBRARY_PREFIX",
    ]

    def __init__(self):
        self.__attrs = {}
        for i in self.items:
            key = i.lower()
            value = os.environ.get(i, None)
            if value is None:
                raise RuntimeError(f"{i} not set in environment")
            self.__attrs[key] = Path(value)

    def __getattr__(self, name):
        if name in self.__attrs:
            return self.__attrs[name]
        else:
            raise AttributeError


def subs(line, substitutes):
    t = AtTemplate(line)
    return t.substitute(substitutes)


def copy_and_rename(source, target, substitutes):
    with open(source, "r") as r:
        with open(target, "w") as w:
            for line in r:
                w.write(subs(line, substitutes))


def first(items, cond):
    return next(item for item in items if cond(item))


def agree_to_license(manifest, auto_accept: bool = False):
    tools = first(
        manifest["channelItems"],
        lambda x: x["id"] == "Microsoft.VisualStudio.Product.BuildTools",
    )
    resource = first(tools["localizedResources"], lambda x: x["language"] == "en-us")
    license = resource["license"]

    if not auto_accept:
        accept = input(f"Do you accept Visual Studio license at {license} [Y/N] ? ")
        if not accept or accept[0].lower() != "y":
            exit(0)
    else:
        print(
            "By providing flag '--accept-license', you automatically accepted"
            f"the Visual Studio license at {license}"
        )


def get_msi_cabs(msi):
    index = 0
    while True:
        index = msi.find(b".cab", index + 4)
        if index < 0:
            return
        yield msi[index - 32 : index + 4].decode("ascii")


def install_vc_components(
    components,
    packages,
    msvc_ver,
    env,
    install_dir,
    host,
    target,
    activation_hooks_dir,
    deactivation_hooks_dir,
):
    print(f"Starting installation of VC component(s) {components}")
    vc_packages = {
        "msvc": [
            # MSVC binaries
            f"microsoft.vc.{msvc_ver}.tools.host{host}.target{target}.base",
            f"microsoft.vc.{msvc_ver}.tools.host{host}.target{target}.res.base",
        ],
        "crt": [
            # MSVC headers
            f"microsoft.vc.{msvc_ver}.crt.headers.base",
            # MSVC libs
            f"microsoft.vc.{msvc_ver}.crt.{target}.desktop.base",
            f"microsoft.vc.{msvc_ver}.crt.{target}.store.base",
            # MSVC runtime source
            f"microsoft.vc.{msvc_ver}.crt.source.base",
        ],
        "asan": [
            # ASAN
            f"microsoft.vc.{msvc_ver}.asan.headers.base",
            f"microsoft.vc.{msvc_ver}.asan.{target}.base",
            # MSVC redist
            # f"microsoft.vc.{msvc_ver}.crt.redist.x64.base",
        ],
    }
    selected_msvc_components = [
        c for component in components for c in vc_packages.get(component, [])
    ]
    print(f"Selected payload(s) are: {selected_msvc_components}")

    total_download = 0
    print("Starting download")
    for pkg in selected_msvc_components:
        p = first(packages[pkg], lambda p: p.get("language") in (None, "en-US"))
        for payload in p["payloads"]:
            with tempfile.TemporaryFile() as f:
                data = download_progress(payload["url"], payload["sha256"], pkg, f)
                total_download += len(data)
                with zipfile.ZipFile(f) as z:
                    for name in z.namelist():
                        if name.startswith("Contents/"):
                            out = install_dir / Path(name).relative_to("Contents")
                            out.parent.mkdir(parents=True, exist_ok=True)
                            out.write_bytes(z.read(name))

    print(f"VC component(s) total download: {total_download>>20} MB")

    msvc_install_dir = list((install_dir / "VC/Tools/MSVC").glob("*"))
    if msvc_install_dir:
        msvcv = msvc_install_dir[0].name
    else:
        print("Error during vc components installation")
        exit(1)

    print("Cleaning unused components")
    for f in ["Auxiliary", f"lib/{target}/store", f"lib/{target}/uwp"]:
        shutil.rmtree(install_dir / "VC/Tools/MSVC" / msvcv / f, ignore_errors=True)

    for arch in ["x86", "x64", "arm", "arm64"]:
        if arch != target:
            shutil.rmtree(
                install_dir / "VC/Tools/MSVC" / msvcv / f"bin/Host{arch}",
                ignore_errors=True,
            )

    msvc_substitutes = {
        "PREFIX": install_dir,
        "MSVC_VERSION": msvcv,
        "HOST_ARCH": host,
        "TARGET_ARCH": target,
    }

    print("Creating activation and deactivation hooks")
    copy_and_rename(
        env.recipe_dir / "activate_msvc.bat",
        activation_hooks_dir / "vs_buildtools-msvc.bat",
        msvc_substitutes,
    )
    copy_and_rename(
        env.recipe_dir / "deactivate_msvc.bat",
        deactivation_hooks_dir / "vs_buildtools-msvc.bat",
        msvc_substitutes,
    )
    print("VC component(s) successfully installed")


def install_sdk(
    packages,
    sdk_pkg_id,
    env,
    install_dir,
    host,
    target,
    activation_hooks_dir,
    deactivation_hooks_dir,
):
    dst = install_dir / "SDK" / f"{target}"

    sdk_packages = [
        # Windows SDK tools (like rc.exe & mt.exe)
        "Windows SDK for Windows Store Apps Tools-x86_en-us.msi",
        # Windows SDK headers
        "Windows SDK for Windows Store Apps Headers-x86_en-us.msi",
        "Windows SDK Desktop Headers x86-x86_en-us.msi",
        # Windows SDK libs
        "Windows SDK for Windows Store Apps Libs-x86_en-us.msi",
        f"Windows SDK Desktop Libs {target}-x86_en-us.msi",
        # CRT headers & libs
        "Universal CRT Headers Libraries and Sources-x86_en-us.msi",
        # CRT redist
        # "Universal CRT Redistributable-x86_en-us.msi",
    ]

    with tempfile.TemporaryDirectory() as d:
        dst = Path(d)

        sdk_pkg = packages[sdk_pkg_id][0]
        sdk_pkg = packages[first(sdk_pkg["dependencies"], lambda x: True).lower()][0]

        msi = []
        cabs = []

        total_download = 0
        for pkg in sdk_packages:
            payload = first(
                sdk_pkg["payloads"], lambda p: p["fileName"] == f"Installers\\{pkg}"
            )
            msi.append(dst / pkg)
            with open(dst / pkg, "wb") as f:
                data = download_progress(payload["url"], payload["sha256"], pkg, f)
            total_download += len(data)
            cabs += list(get_msi_cabs(data))
        print(f"SDK total download: {total_download>>20} MB")

        for pkg in cabs:
            payload = first(
                sdk_pkg["payloads"], lambda p: p["fileName"] == f"Installers\\{pkg}"
            )
            with open(dst / pkg, "wb") as f:
                download_progress(payload["url"], payload["sha256"], pkg, f)

        print("Unpacking msi files")
        for m in msi:
            subprocess.check_call(
                [
                    "msiexec.exe",
                    "/a",
                    m,
                    "/quiet",
                    "/qn",
                    f"TARGETDIR={install_dir.resolve()}",
                ]
            )

        sdkv = list((install_dir / "Windows Kits/10/bin").glob("*"))[0].name

    shutil.rmtree(install_dir / "Common7", ignore_errors=True)

    print("Cleaning unused components")
    for f in install_dir.glob("*.msi"):
        f.unlink()

    for f in [
        "Catalogs",
        "DesignTime",
        f"bin/{sdkv}/chpe",
        f"Lib/{sdkv}/ucrt_enclave",
    ]:
        shutil.rmtree(install_dir / "Windows Kits/10" / f, ignore_errors=True)

    for arch in ["x86", "x64", "arm", "arm64"]:
        if arch != target:
            shutil.rmtree(
                install_dir / "Windows Kits/10/bin" / sdkv / arch,
                ignore_errors=True,
            )
            shutil.rmtree(
                install_dir / "Windows Kits/10/Lib" / sdkv / "ucrt" / arch,
                ignore_errors=True,
            )
            shutil.rmtree(
                install_dir / "Windows Kits/10/Lib" / sdkv / "um" / arch,
                ignore_errors=True,
            )
    print("SDK successfully installed")


def parse_args():
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
    ap.add_argument(
        "--sdk-win-version",
        help="Allow to check compatibility between SDK and Windows version",
    )
    ap.add_argument("--components", action="extend", nargs="+", type=str)
    ap.add_argument(
        "--discard", nargs="*", help="Extra arguments which will be discarded"
    )
    return ap.parse_args()


def main():
    # other architectures may work or may not - not really tested
    HOST = "x64"  # or x86
    TARGET = "x64"  # or x86, arm, arm64

    MANIFEST_URL = "https://aka.ms/vs/17/release/channel"

    args = parse_args()

    OUTPUT = Path(os.environ["LIBRARY_PREFIX"]) / "vs_buildtools"  # output folder
    print(f"Installation directory set to '{OUTPUT}'")

    # get and validate components
    if args.components is None:
        print("Please select at least one component using '--components' CLI option")
        exit(0)
    components = set(args.components)
    available_components = {"msvc", "asan", "sdk", "crt"}
    if not components.issubset(available_components):
        raise ValueError(f"Invalid components {components - available_components}")

    manifest = json.loads(download(MANIFEST_URL))
    vs_workload = first(
        manifest["channelItems"],
        lambda x: x["id"] == "Microsoft.VisualStudio.Manifests.VisualStudio",
    )
    payload = vs_workload["payloads"][0]["url"]

    vs_manifest = json.loads(download(payload))

    packages = {}
    for p in vs_manifest["packages"]:
        packages.setdefault(p["id"].lower(), []).append(p)

    msvc = {}
    sdk = {}

    for pkg_id, pkg in packages.items():
        if pkg_id.startswith(
            "Microsoft.VisualStudio.Component.VC.".lower()
        ) and pkg_id.endswith(".x86.x64".lower()):
            pkg_ver = ".".join(pkg_id.split(".")[4:7])
            if pkg_ver[0].isnumeric():
                msvc[pkg_ver] = pkg_id
        elif pkg_id.startswith(
            "Microsoft.VisualStudio.Component.Windows10SDK.".lower()
        ) or pkg_id.startswith(
            "Microsoft.VisualStudio.Component.Windows11SDK.".lower()
        ):
            pkg_ver = pkg_id.split(".")[-1]
            if pkg_ver.isnumeric():
                sdk[pkg_ver] = pkg_id

    if args.show_versions:
        print("MSVC versions:", " ".join(sorted(msvc.keys())))
        print("Windows SDK versions:", " ".join(sorted(sdk.keys())))
        exit(0)

    env = Environment()
    activation_hooks_dir = Path(env.prefix) / "etc" / "conda" / "activate.d"
    deactivation_hooks_dir = Path(env.prefix) / "etc" / "conda" / "deactivate.d"
    os.makedirs(activation_hooks_dir, exist_ok=True)
    os.makedirs(deactivation_hooks_dir, exist_ok=True)

    OUTPUT.mkdir(exist_ok=True, parents=True)

    agree_to_license(manifest, args.accept_license)

    if any(component in components for component in ["msvc", "crt", "asan"]):
        if args.msvc_version in msvc:
            msvc_pkg_id = msvc[args.msvc_version]
            msvc_ver = ".".join(msvc_pkg_id.split(".")[4:-2])
            install_vc_components(
                components,
                packages,
                msvc_ver,
                env,
                OUTPUT,
                HOST,
                TARGET,
                activation_hooks_dir=activation_hooks_dir,
                deactivation_hooks_dir=deactivation_hooks_dir,
            )
        else:
            print(f"Available MSVC versions are: {msvc}")
            exit(f"Unknown MSVC version: {args.msvc_version}")

    if "sdk" in components:
        sdk_version = args.sdk_version
        sdk_win_version = args.sdk_win_version
        if sdk_version in sdk:
            sdk_pkg_id = sdk[args.sdk_version]
            if not sdk_win_version or sdk_win_version.lower() not in sdk_pkg_id:
                exit(
                    f"SDK is not compatible with Windows version '{sdk_win_version}'."
                    f"\nAvailable SDKs are: {sdk}"
                )

            install_sdk(
                packages,
                sdk_pkg_id,
                env,
                OUTPUT,
                HOST,
                TARGET,
                activation_hooks_dir=activation_hooks_dir,
                deactivation_hooks_dir=deactivation_hooks_dir,
            )
        else:
            print(f"Available SDK versions are: {sdk}")
            exit(f"Unknown Windows SDK version: {sdk_version}")


if __name__ == "__main__":
    main()
