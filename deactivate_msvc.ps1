$env:PATH = $env:PATH -replace [regex]::Escape($env:MSVC_ADDITION_TO_PATH+ ';'), ''