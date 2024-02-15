@@echo off

set ROOT=@{ROOT_PREFIX}
set MSVC_VERSION=@{MSVC_VERSION}
set MSVC_HOST=Host@{HOST_ARCH}
set MSVC_ARCH=@{TARGET_ARCH}

set MSVC_ROOT=%ROOT%\\VC\\Tools\\MSVC\\%MSVC_VERSION%

set VCToolsInstallDir=%MSVC_ROOT%\\
set MSVC_ADDITION_TO_PATH=%MSVC_ROOT%\\bin\\%MSVC_HOST%\\%MSVC_ARCH%
set PATH=%MSVC_ADDITION_TO_PATH%;%PATH%
set INCLUDE=%MSVC_ROOT%\\include;%INCLUDE%
set LIB=%MSVC_ROOT%\\lib\\%MSVC_ARCH%;%LIB%
