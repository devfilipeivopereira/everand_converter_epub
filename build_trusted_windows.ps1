$ErrorActionPreference = 'Stop'

$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ReleaseRoot = Join-Path $Root 'release'
$Target = Join-Path $ReleaseRoot 'EverandEPUBStudio-Trusted'
$ExpectedTarget = [IO.Path]::GetFullPath((Join-Path $Root 'release\EverandEPUBStudio-Trusted'))
if ([IO.Path]::GetFullPath($Target) -ne $ExpectedTarget) {
    throw 'Destino inesperado; a compilação foi interrompida.'
}

$PythonExecutable = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExecutable)) {
    throw 'Python 3.12 oficial não foi encontrado.'
}
$PythonRoot = Split-Path -Parent $PythonExecutable
$PythonW = Join-Path $PythonRoot 'pythonw.exe'
$PythonSignature = Get-AuthenticodeSignature -LiteralPath $PythonW
if ($PythonSignature.Status -ne 'Valid' -or $PythonSignature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    throw 'O runtime oficial do Python não possui a assinatura confiável esperada.'
}

$PyInstallerRuntime = Join-Path $Root 'release\EverandEPUBStudio\_internal'
if (-not (Test-Path -LiteralPath (Join-Path $PyInstallerRuntime 'PySide6\QtWidgets.pyd'))) {
    throw 'Execute build_windows.ps1 antes de gerar a edição compatível com Smart App Control.'
}

if (Test-Path -LiteralPath $Target) {
    $Resolved = [IO.Path]::GetFullPath($Target)
    if ($Resolved -ne $ExpectedTarget) {
        throw "Recusa ao limpar destino inesperado: $Resolved"
    }
    Remove-Item -LiteralPath $Resolved -Recurse -Force
}
New-Item -ItemType Directory -Path $Target | Out-Null

Copy-Item -LiteralPath $PythonW -Destination (Join-Path $Target 'EverandEPUBStudio.exe')
foreach ($Name in @('python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    Copy-Item -LiteralPath (Join-Path $PythonRoot $Name) -Destination (Join-Path $Target $Name)
}
Copy-Item -LiteralPath (Join-Path $PythonRoot 'DLLs') -Destination (Join-Path $Target 'DLLs') -Recurse

$LibrarySource = Join-Path $PythonRoot 'Lib'
$LibraryTarget = Join-Path $Target 'python_lib'
& robocopy $LibrarySource $LibraryTarget /E /NFL /NDL /NJH /NJS /NP /XD site-packages __pycache__ test tests idlelib ensurepip /XF *.pyc | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "A cópia da biblioteca do Python falhou com código $LASTEXITCODE."
}

$AppTarget = Join-Path $Target 'app'
New-Item -ItemType Directory -Path $AppTarget | Out-Null
& robocopy (Join-Path $Root 'everand_app') (Join-Path $AppTarget 'everand_app') /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "A cópia do aplicativo falhou com código $LASTEXITCODE."
}
Copy-Item -LiteralPath (Join-Path $Root 'everand_to_epub.py') -Destination $AppTarget

$AppLib = Join-Path $Target 'app_lib'
New-Item -ItemType Directory -Path $AppLib | Out-Null
Copy-Item -LiteralPath (Join-Path $PyInstallerRuntime 'PySide6') -Destination (Join-Path $AppLib 'PySide6') -Recurse
Copy-Item -LiteralPath (Join-Path $PyInstallerRuntime 'shiboken6') -Destination (Join-Path $AppLib 'shiboken6') -Recurse
$VenvSite = Join-Path $Root '.venv-build\Lib\site-packages'
foreach ($Name in @('__init__.py', '_config.py', '_git_pyside_version.py')) {
    Copy-Item -LiteralPath (Join-Path $VenvSite "PySide6\$Name") -Destination (Join-Path $AppLib "PySide6\$Name")
}
foreach ($Name in @('__init__.py', '_config.py', '_git_shiboken_module_version.py')) {
    Copy-Item -LiteralPath (Join-Path $VenvSite "shiboken6\$Name") -Destination (Join-Path $AppLib "shiboken6\$Name")
}

Copy-Item -LiteralPath (Join-Path $Root 'portable_support\python312._pth') -Destination $Target
Copy-Item -LiteralPath (Join-Path $Root 'portable_support\sitecustomize.py') -Destination $Target
Copy-Item -LiteralPath (Join-Path $Root 'GUIA_DE_USO.md') -Destination $Target -ErrorAction SilentlyContinue

$NativeFiles = Get-ChildItem -LiteralPath $Target -Recurse -File | Where-Object { $_.Extension -in '.exe', '.dll', '.pyd' }
$Invalid = foreach ($File in $NativeFiles) {
    $Signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if ($Signature.Status -ne 'Valid') {
        [PSCustomObject]@{ File = $File.FullName; Status = $Signature.Status }
    }
}
if (@($Invalid).Count -gt 0) {
    $Invalid | Format-Table | Out-String | Write-Error
    throw 'A edição confiável contém componente nativo sem assinatura válida.'
}

$Zip = Join-Path $ReleaseRoot 'EverandEPUBStudio-Trusted.zip'
if (Test-Path -LiteralPath $Zip) {
    Remove-Item -LiteralPath $Zip -Force
}
Compress-Archive -LiteralPath $Target -DestinationPath $Zip -CompressionLevel Optimal

$Executable = Join-Path $Target 'EverandEPUBStudio.exe'
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Executable).Hash
Write-Host "Aplicativo confiável criado em: $Executable"
Write-Host "Componentes nativos assinados: $($NativeFiles.Count)"
Write-Host "SHA-256: $Hash"
