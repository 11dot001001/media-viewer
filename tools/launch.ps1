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
    $installed = $false
    foreach ($channel in @('https://conda.anaconda.org/conda-forge', 'https://prefix.dev/conda-forge')) {
        Write-Host "Installing runtime from $channel ..."
        & $manager create -y --no-rc --override-channels --repodata-ttl 0 --retry-clean-cache -c $channel -p $prefix python=3.12 pip ffmpeg
        if ($LASTEXITCODE -eq 0) { $installed = $true; break }
        Write-Host 'Package download or resolution failed; trying the alternate server...'
    }
    if (!$installed) { throw 'Cannot install runtime from either server. Check internet/proxy access to conda.anaconda.org and prefix.dev, then launch again. No manual Python installation is needed.' }
    Set-Content "$runtime/ready" '1'
}
& $manager run -p $prefix python tools/bootstrap.py @args
exit $LASTEXITCODE
