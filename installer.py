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
    host,
    target,
    prefixes
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
                            out = prefixes.install_prefix / Path(name).relative_to("Contents")
                            out.parent.mkdir(parents=True, exist_ok=True)
                            out.write_bytes(z.read(name))

    print(f"VC component(s) total download: {total_download>>20} MB")

    msvc_install_dir = list((prefixes.install_prefix / "VC/Tools/MSVC").glob("*"))
    if msvc_install_dir:
        msvcv = msvc_install_dir[0].name
    else:
        print("Error during vc components installation")
        exit(1)

    print("Cleaning unused components")
    for f in ["Auxiliary", f"lib/{target}/store", f"lib/{target}/uwp"]:
        shutil.rmtree(prefixes.install_prefix / "VC/Tools/MSVC" / msvcv / f, ignore_errors=True)

    for arch in ["x86", "x64", "arm", "arm64"]:
        if arch != target:
            shutil.rmtree(
                prefixes.install_prefix / "VC/Tools/MSVC" / msvcv / f"bin/Host{arch}",
                ignore_errors=True,
            )

    if prefixes.scripts_root_prefix_placeholder:
        scripts_root_prefix_placeholder = prefixes.scripts_root_prefix_placeholder
    else:
        if prefixes.install_prefix.is_absolute():
            scripts_root_prefix_placeholder = prefixes.install_prefix.relative_to(prefixes.root_prefix)
        else:
            scripts_root_prefix_placeholder = prefixes.install_prefix

    msvc_substitutes = {
        "ROOT_PREFIX": scripts_root_prefix_placeholder,
        "MSVC_VERSION": msvcv,
        "HOST_ARCH": host,
        "TARGET_ARCH": target,
    }

    if prefixes.activation_scripts_prefix or prefixes.deactivation_scripts_prefix:
        print("Creating activation and deactivation hooks")
        tmpl_path = Path(__file__).parent

        if prefixes.activation_scripts_prefix:
            copy_and_rename(
                tmpl_path / "activate_msvc.bat",
                prefixes.activation_scripts_prefix / "vs2022_buildtools-msvc.bat",
                msvc_substitutes,
            )
            copy_and_rename(
                tmpl_path / "activate_msvc.ps1",
                prefixes.activation_scripts_prefix / "vs2022_buildtools-msvc.ps1",
                msvc_substitutes,
            )
        if prefixes.deactivation_scripts_prefix:
            copy_and_rename(
                tmpl_path / "deactivate_msvc.bat",
                prefixes.deactivation_scripts_prefix / "vs2022_buildtools-msvc.bat",
                msvc_substitutes,
            )
            copy_and_rename(
                tmpl_path / "deactivate_msvc.ps1",
                prefixes.deactivation_scripts_prefix / "vs2022_buildtools-msvc.ps1",
                msvc_substitutes,
            )

    print("VC component(s) successfully installed")


def install_sdk(
    packages,
    sdk_pkg_id,
    host,
    target,
    prefixes,
):
    dst = prefixes.install_prefix / "SDK" / f"{target}"

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
                    f"TARGETDIR={prefixes.install_prefix.resolve()}",
                ]
            )

        sdkv = list((prefixes.install_prefix / "Windows Kits/10/bin").glob("*"))[0].name

    shutil.rmtree(prefixes.install_prefix / "Common7", ignore_errors=True)

    print("Cleaning unused components")
    for f in prefixes.install_prefix.glob("*.msi"):
        f.unlink()

    for f in [
        "Catalogs",
        "DesignTime",
        f"bin/{sdkv}/chpe",
        f"Lib/{sdkv}/ucrt_enclave",
    ]:
        shutil.rmtree(prefixes.install_prefix / "Windows Kits/10" / f, ignore_errors=True)

    for arch in ["x86", "x64", "arm", "arm64"]:
        if arch != target:
            shutil.rmtree(
                prefixes.install_prefix / "Windows Kits/10/bin" / sdkv / arch,
                ignore_errors=True,
            )
            shutil.rmtree(
                prefixes.install_prefix / "Windows Kits/10/Lib" / sdkv / "ucrt" / arch,
                ignore_errors=True,
            )
            shutil.rmtree(
                prefixes.install_prefix / "Windows Kits/10/Lib" / sdkv / "um" / arch,
                ignore_errors=True,
            )

    if prefixes.scripts_root_prefix_placeholder:
        scripts_root_prefix_placeholder = prefixes.scripts_root_prefix_placeholder
    else:
        if prefixes.install_prefix.is_absolute():
            scripts_root_prefix_placeholder = prefixes.install_prefix.relative_to(prefixes.root_prefix)
        else:
            scripts_root_prefix_placeholder = prefixes.install_prefix

    sdk_substitutes = {
        "ROOT_PREFIX": scripts_root_prefix_placeholder,
        "SDK_VERSION": sdkv,
        "SDK_TARGET_ARCH": target
    }

    if prefixes.activation_scripts_prefix or prefixes.deactivation_scripts_prefix:
        print("Creating activation and deactivation hooks")
        tmpl_path = Path(__file__).parent

        if prefixes.activation_scripts_prefix:
            copy_and_rename(
                tmpl_path / "activate_sdk.bat",
                prefixes.activation_scripts_prefix / "vs2022_buildtools-win-sdk.bat",
                sdk_substitutes,
            )
            copy_and_rename(
                tmpl_path / "activate_sdk.ps1",
                prefixes.activation_scripts_prefix / "vs2022_buildtools-win-sdk.ps1",
                sdk_substitutes,
            )
        if prefixes.deactivation_scripts_prefix:
            copy_and_rename(
                tmpl_path / "deactivate_sdk.bat",
                prefixes.deactivation_scripts_prefix / "vs2022_buildtools-win-sdk.bat",
                sdk_substitutes,
            )
            copy_and_rename(
                tmpl_path / "deactivate_sdk.ps1",
                prefixes.deactivation_scripts_prefix / "vs2022_buildtools-win-sdk.ps1",
                sdk_substitutes,
            )

    print("SDK successfully installed")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("install_prefix", help="Get installation prefix, absolute or relative to the root prefix if provided")
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
    ap.add_argument("--root-prefix", help="Get the root prefix")
    ap.add_argument("--scripts-prefix", help="Get installation prefix, relative to the root prefix")
    ap.add_argument("--activation-scripts-prefix", help="Get activation scripts prefix")
    ap.add_argument("--deactivation-scripts-prefix", help="Get deactivation scripts prefix")
    ap.add_argument("--scripts-root-prefix-placeholder", help="Get the placeholder to use instead of root prefix in (de)activation scripts")
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

from dataclasses import dataclass

@dataclass
class Prefixes:
    root_prefix: None = None
    install_prefix: None= None
    scripts_prefix: None= None
    activation_scripts_prefix: None= None
    deactivation_scripts_prefix: None= None
    scripts_root_prefix_placeholder: None= None


def get_prefixes(args):

    prefixes = Prefixes()

    if args.root_prefix:
        prefixes.root_prefix = Path(args.root_prefix)
    else:
        prefixes.root_prefix = Path.cwd()
    
    if args.install_prefix:
        prefixes.install_prefix = Path(args.install_prefix)
        if prefixes.install_prefix.is_absolute() and prefixes.install_prefix.relative_to(prefixes.root_prefix) is None:
            exit(f"Invalid installation prefix '{args.install_prefix}'")
    else:
        exit(f"Invalid installation prefix '{args.install_prefix}'")

    if args.scripts_prefix:
        prefixes.scripts_prefix = Path(args.scripts_prefix)

    if args.activation_scripts_prefix:
        prefixes.activation_scripts_prefix = Path(args.activation_scripts_prefix)
        if prefixes.scripts_prefix and prefixes.activation_scripts_prefix.is_absolute() and prefixes.activation_scripts_prefix.relative_to(prefixes.scripts_prefix) is None:
            exit(f"Invalid activation prefix '{args.activation_scripts_prefix}'")

    if args.deactivation_scripts_prefix:
        prefixes.deactivation_scripts_prefix = Path(args.deactivation_scripts_prefix)
        if prefixes.scripts_prefix and prefixes.deactivation_scripts_prefix.is_absolute() and prefixes.deactivation_scripts_prefix.relative_to(prefixes.scripts_prefix) is None:
            exit(f"Invalid deactivation prefix '{args.deactivation_scripts_prefix}'")

    if args.scripts_root_prefix_placeholder:
        prefixes.scripts_root_prefix_placeholder = args.scripts_root_prefix_placeholder

    if prefixes.install_prefix:
        prefixes.install_prefix.mkdir(exist_ok=True, parents=True)
    if prefixes.activation_scripts_prefix:
        prefixes.activation_scripts_prefix.mkdir(exist_ok=True, parents=True)
    if prefixes.deactivation_scripts_prefix:
        prefixes.deactivation_scripts_prefix.mkdir(exist_ok=True, parents=True)

    return prefixes


def main():
    # other architectures may work or may not - not really tested
    HOST = "x64"  # or x86
    TARGET = "x64"  # or x86, arm, arm64

    # vs2022 manifest
    MANIFEST_URL = "https://aka.ms/vs/17/release/channel"

    args = parse_args()

    manifest = json.loads(download(MANIFEST_URL))
    agree_to_license(manifest, args.accept_license)

    prefixes = get_prefixes(args)
    print(f"Installation prefix set to '{prefixes.install_prefix}'")

    if args.components is None:
        print("Please select at least one component using '--components' CLI option")
        exit(0)
    components = set(args.components)
    available_components = {"msvc", "asan", "sdk", "crt"}
    if not components.issubset(available_components):
        raise ValueError(f"Invalid components {components - available_components}")
    
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
       
    if any(component in components for component in ["msvc", "crt", "asan"]):
        if args.msvc_version in msvc:
            msvc_pkg_id = msvc[args.msvc_version]
            msvc_ver = ".".join(msvc_pkg_id.split(".")[4:-2])
            install_vc_components(
                components,
                packages,
                msvc_ver,
                HOST,
                TARGET,
                prefixes
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
                HOST,
                TARGET,
                prefixes
            )
        else:
            print(f"Available SDK versions are: {sdk}")
            exit(f"Unknown Windows SDK version: {sdk_version}")


if __name__ == "__main__":
    main()
