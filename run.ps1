# PES 2021 server on your Windows PC: no Docker, no administrator rights.
#
#   irm https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/run.ps1 | iex
#
# Downloads the newest version of the server program (only when there is a new one), then runs it in this
# window. Ctrl+C or closing the window stops it. Next time, run the same line: you always get the newest version.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # Windows PowerShell 5 downloads much faster without the bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$rel = 'https://github.com/MohamedAliRashad/pes2021-private-server/releases/latest/download'
$dir = Join-Path $env:LOCALAPPDATA 'PES2021 Server'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$bin = Join-Path $dir 'pes2021-server.exe'
$verFile = Join-Path $dir 'program-version.txt'
$have = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { '' }
try { $latest = ([string](Invoke-RestMethod -Uri "$rel/version.txt" -TimeoutSec 20)).Trim() } catch { $latest = '' }

if ($latest -and ($latest -ne $have -or -not (Test-Path $bin))) {
  if ($have -and (Test-Path $bin)) { Write-Host "Updating the PES 2021 server: $have  ->  $latest" }
  else { Write-Host "Downloading the PES 2021 server, version $latest (about 15 MB)..." }
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$rel/pes2021-server-windows-x64.exe" -OutFile "$bin.new" -TimeoutSec 600
    Move-Item -Force "$bin.new" $bin
    Set-Content -Path $verFile -Value $latest
  } catch {
    Remove-Item -Force "$bin.new" -ErrorAction SilentlyContinue
    if (-not (Test-Path $bin)) { Write-Host "The download failed. Check the internet connection and run the line again."; return }
    Write-Host "The download failed; starting the version you have ($have)."
  }
} elseif (-not $latest) {
  if (-not (Test-Path $bin)) { Write-Host "Can't reach GitHub to download the server. Check the internet connection and run the line again."; return }
  Write-Host "Couldn't check for a newer version (offline?); starting the version you have ($have)."
}
& $bin @args
