$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONHOME = ''
$env:PYTHONPATH = ''
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
$env:MAMBA_ROOT_PREFIX = Join-Path (Get-Location) '.runtime/mamba'
$runtime = Join-Path (Get-Location) '.runtime'
$manager = Join-Path $runtime 'micromamba.exe'
$prefix = Join-Path $runtime 'env'
New-Item -ItemType Directory -Force $runtime | Out-Null
if (!(Test-Path $manager)) {
    Write-Host 'Downloading application setup tool...'
    Invoke-WebRequest 'https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64' -OutFile "$manager.download" -UseBasicParsing
    Move-Item -LiteralPath "$manager.download" -Destination $manager -Force
}
if (!(Test-Path "$runtime/ready")) {
    & $manager create -y --no-rc --override-channels -c conda-forge -p $prefix python=3.12 pip ffmpeg
    if ($LASTEXITCODE) { throw 'Runtime installation failed. Please retry.' }
    Set-Content "$runtime/ready" '1'
}
& $manager run -p $prefix python tools/bootstrap.py @args
exit $LASTEXITCODE
