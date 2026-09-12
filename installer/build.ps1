# Build the Discord TTS Bot Windows installer (run locally or by .github/workflows/release.yml):
#   build venv (uv) -> PyInstaller (installer\tts_bot.spec) -> Inno Setup (installer\tts_bot.iss)
# Output: build\release\DiscordTTSBot-Setup-<version>.exe   (version = src\app_version.py)
# Requires: uv, Inno Setup 6 (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"
$build = Join-Path $root "build"

$m = Select-String -Path (Join-Path $root "src\app_version.py") -Pattern 'VERSION\s*=\s*"([^"]+)"'
if (-not $m) { throw "VERSION not found in src\app_version.py" }
$version = $m.Matches[0].Groups[1].Value

if (-not (Test-Path $py)) {
    # uv-managed CPython, so the bundle never picks up conda (anaconda) DLLs from PATH
    uv venv $venv --python 3.11 --managed-python
    if ($LASTEXITCODE) { throw "venv creation failed" }
}
uv pip install --python $py -r (Join-Path $root "requirements.txt") pyinstaller
if ($LASTEXITCODE) { throw "dependency install failed" }

& $py -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $build "pyinstaller") --workpath (Join-Path $build "work") `
    (Join-Path $PSScriptRoot "tts_bot.spec")
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup" }
& $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot "tts_bot.iss")
if ($LASTEXITCODE) { throw "Inno Setup failed" }
