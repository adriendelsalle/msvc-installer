@@echo off

set ROOT=%CONDA_PREFIX%\Library\vs_buildtools
set SDK_VERSION=@{SDK_VERSION}
set SDK_ARCH=@{SDK_TARGET_ARCH}

set SDK_INCLUDE=%ROOT%\\Windows Kits\\10\\Include\\%SDK_VERSION%
set SDK_LIBS=%ROOT%\\Windows Kits\\10\\Lib\\%SDK_VERSION%

set WIN_SDK_ADDITION_TO_PATH=%ROOT%\\Windows Kits\\10\\bin\\%SDK_VERSION%\\%SDK_ARCH%;%ROOT%\\Windows Kits\\10\\bin\\%SDK_VERSION%\\%SDK_ARCH%\\ucrt
set PATH=%WIN_SDK_ADDITION_TO_PATH%;%PATH%
set INCLUDE=%SDK_INCLUDE%\\ucrt;%SDK_INCLUDE%\\shared;%SDK_INCLUDE%\\um;%SDK_INCLUDE%\\winrt;%SDK_INCLUDE%\\cppwinrt;%INCLUDE%
set LIB=%SDK_LIBS%\\ucrt\\%SDK_ARCH%;%SDK_LIBS%\\um\\%SDK_ARCH%;%LIB%
