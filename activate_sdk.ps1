$ROOT = "$env:CONDA_PREFIX\Library\vs_buildtools"
$SDK_VERSION = "@{SDK_VERSION}"
$SDK_ARCH = "@{SDK_TARGET_ARCH}"

$SDK_INCLUDE = "$ROOT\Windows Kits\10\Include\$SDK_VERSION"
$SDK_LIBS = "$ROOT\Windows Kits\10\Lib\$SDK_VERSION"

$env:WIN_SDK_ADDITION_TO_PATH = "$ROOT\Windows Kits\10\bin\$SDK_VERSION\$SDK_ARCH;$ROOT\Windows Kits\10\bin\$SDK_VERSION\$SDK_ARCH\ucrt"
$env:PATH = "$env:WIN_SDK_ADDITION_TO_PATH;$env:PATH"
$env:INCLUDE = "$SDK_INCLUDE\ucrt;$SDK_INCLUDE\shared;$SDK_INCLUDE\um;$SDK_INCLUDE\winrt;$SDK_INCLUDE\cppwinrt;$env:INCLUDE"
$env:LIB = "$SDK_LIBS\ucrt\$SDK_ARCH;$SDK_LIBS\um\$SDK_ARCH;$env:LIB"
