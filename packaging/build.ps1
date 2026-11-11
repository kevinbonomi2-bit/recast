# Builds dist\Recast (a standalone folder with Recast.exe) and, when Inno Setup is
# installed, dist\Recast-Setup-<version>.exe. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$version = & $py -c "import recast; print(recast.__version__)"

& $py brand\build_brand.py
& $py -m PyInstaller --noconfirm --clean --windowed --name Recast `
    --icon brand\recast.ico `
    --distpath dist --workpath build `
    --exclude-module tkinter --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtPdf `
    --exclude-module pytest --exclude-module onnxruntime --exclude-module scipy `
    --exclude-module pandas --exclude-module matplotlib `
    --exclude-module pymupdf.layout --exclude-module pymupdf4llm --exclude-module tabulate `
    --exclude-module yaml --exclude-module psutil `
    --hidden-import imagecodecs._jpegxr --hidden-import imagecodecs._shared_cython --hidden-import imagecodecs._imcd `
    packaging\recast_main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
# Software OpenGL fallback and Qt's UI translations: unused, ~25 MB.
Remove-Item dist\Recast\_internal\PySide6\opengl32sw.dll -ErrorAction SilentlyContinue
Remove-Item dist\Recast\_internal\PySide6\translations -Recurse -ErrorAction SilentlyContinue
# Qt parts that plugins drag in but Recast never uses (QML, Quick, Qt's PDF viewer, the on-screen
# keyboard, OpenSSL for Qt networking: Recast only talks over local pipes), ~30 MB.
$qt = "dist\Recast\_internal\PySide6"
foreach ($f in @("Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlMeta.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
                 "Qt6Pdf.dll", "Qt6VirtualKeyboard.dll", "plugins\imageformats\qpdf.dll",
                 "plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll", "plugins\tls\qopensslbackend.dll")) {
    Remove-Item (Join-Path $qt $f) -ErrorAction SilentlyContinue
}
Remove-Item dist\Recast\_internal\libcrypto-3-x64.dll, dist\Recast\_internal\libssl-3-x64.dll -ErrorAction SilentlyContinue

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DAppVersion=$version" packaging\recast.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "Installer: dist\Recast-Setup-$version.exe"
} else {
    Write-Host "Inno Setup not found: built dist\Recast only (winget install JRSoftware.InnoSetup for the installer)."
}
