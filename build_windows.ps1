$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv-build\Scripts\python.exe'

function Assert-NativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step falhou com código $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    py -3.12 -m venv (Join-Path $Root '.venv-build')
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $Root 'requirements-build.txt')
Assert-NativeSuccess 'Instalação das dependências'
& $Python (Join-Path $Root 'tools\make_icon.py')
Assert-NativeSuccess 'Geração do ícone'
& $Python -m compileall -q (Join-Path $Root 'everand_app') (Join-Path $Root 'everand_to_epub.py')
Assert-NativeSuccess 'Compilação do código'
& $Python -m unittest discover -s (Join-Path $Root 'tests') -v
Assert-NativeSuccess 'Testes automatizados'
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name EverandEPUBStudio `
    --icon (Join-Path $Root 'assets\EverandEPUBStudio.ico') `
    --version-file (Join-Path $Root 'assets\version_info.txt') `
    --distpath (Join-Path $Root 'release') `
    --workpath (Join-Path $Root 'build\pyinstaller') `
    --specpath (Join-Path $Root 'build') `
    (Join-Path $Root 'everand_launcher.py')
Assert-NativeSuccess 'Empacotamento do aplicativo'

$Executable = Join-Path $Root 'release\EverandEPUBStudio\EverandEPUBStudio.exe'
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "O empacotador terminou sem criar $Executable."
}
Write-Host "Aplicativo criado em: $Executable"
