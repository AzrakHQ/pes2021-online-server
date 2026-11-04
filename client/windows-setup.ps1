<#
  PES 2021 private server - player setup for Windows.

  Points the game's Konami hostnames at your server (hosts file) and trusts the server's
  certificate authority. Run in an *administrator* PowerShell:

      powershell -ExecutionPolicy Bypass -File windows-setup.ps1 -Server 203.0.113.10

  Undo everything:

      powershell -ExecutionPolicy Bypass -File windows-setup.ps1 -Uninstall
#>
param(
    [string]$Server,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this from an administrator PowerShell (right-click PowerShell -> Run as administrator)."
}

$Marker = "# pes2021-private-server"
$Hosts = "$env:SystemRoot\System32\drivers\etc\hosts"
$Names = @(
    "pes21-x64-gate.cs.konami.net",
    "pes21-x64-stun.cs.konami.net",
    "ntl.service.konami.net",
    "ntleu.service.konami.net",
    "ntlus.service.konami.net",
    "ntljp.service.konami.net"
)
$CaName = "PES2021 Private Server CA"

# Remove our previous hosts lines and CA (both for -Uninstall and before re-installing).
$kept = Get-Content $Hosts | Where-Object { $_ -notmatch [regex]::Escape($Marker) }
Set-Content -Path $Hosts -Value $kept -Encoding ASCII
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -eq "CN=$CaName" } | Remove-Item

if ($Uninstall) {
    ipconfig /flushdns | Out-Null
    Write-Host "Removed the hosts entries and the server certificate. The game is back to normal."
    exit 0
}
if (-not $Server) { Write-Error "Give the server address: -Server <IP>" }

# 1. Trust the server's CA (needed for the game's HTTPS network test).
$ca = Join-Path $env:TEMP "pes2021-ca.crt"
Invoke-WebRequest -UseBasicParsing -Uri "http://${Server}:8080/ca.crt" -OutFile $ca
Import-Certificate -FilePath $ca -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Remove-Item $ca

# 2. Point the game's hostnames at the server.
$lines = $Names | ForEach-Object { "$Server  $_  $Marker" }
Add-Content -Path $Hosts -Value $lines -Encoding ASCII
ipconfig /flushdns | Out-Null

Write-Host "Done. PES 2021 will now go online through $Server."
Write-Host "To undo: run this script again with -Uninstall"
