$ROOT = "$env:CONDA_PREFIX\Library\vs_buildtools"
$MSVC_VERSION = "@{MSVC_VERSION}"
$MSVC_HOST = "Host@{HOST_ARCH}"
$MSVC_ARCH = "@{TARGET_ARCH}"

$MSVC_ROOT = "$ROOT\\VC\\Tools\\MSVC\\$MSVC_VERSION"

$VCToolsInstallDir = "$MSVC_ROOT\\"
$env:MSVC_ADDITION_TO_PATH = "$MSVC_ROOT\\bin\\$MSVC_HOST\\$MSVC_ARCH"
$env:PATH = "$env:MSVC_ADDITION_TO_PATH;$env:PATH"
$env:INCLUDE = "$MSVC_ROOT\\include;$env:INCLUDE"
$env:LIB = "$MSVC_ROOT\\lib\\$MSVC_ARCH;$env:LIB"
