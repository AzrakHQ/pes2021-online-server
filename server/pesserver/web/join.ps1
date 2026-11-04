# PES 2021 private server - player setup for Windows.
#   join:  irm @@BASE@@/join | iex
#   undo:  irm @@BASE@@/leave | iex
# Points the game's Konami hostnames at the server (hosts file) and trusts the server's certificate
# authority. Asks for administrator rights once.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$PesServer = "@@SERVER@@"
$PesBase = "@@BASE@@"
$PesUninstall = @@UNINSTALL@@ -eq 1
$PesPage = if ($PesUninstall) { "leave" } else { "join" }

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Opening an administrator window (needed to edit the hosts file and trust the server)..."
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "irm $PesBase/$PesPage | iex")
    return
}

$Marker = "# pes2021-private-server"
$HostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
$Names = "pes21-x64-gate.cs.konami.net", "pes21-x64-stun.cs.konami.net", "ntl.service.konami.net",
         "ntleu.service.konami.net", "ntlus.service.konami.net", "ntljp.service.konami.net"
$CaName = "CN=PES2021 Private Server CA"

# Remove earlier entries (also when switching servers).
$kept = @(Get-Content $HostsFile | Where-Object { $_ -notmatch [regex]::Escape($Marker) })
Set-Content -Path $HostsFile -Value $kept -Encoding ASCII
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -eq $CaName } | Remove-Item

if ($PesUninstall) {
    ipconfig /flushdns | Out-Null
    Write-Host "Done. PES 2021 no longer uses $PesServer." -ForegroundColor Green
    return
}

# The game needs an IP address. Use the first of the server's addresses that answers as this server
# (its LAN address when you're in the same house, its internet address otherwise).
$addrs = "@@ADDRS@@".Split(" ")
$ip = $null
foreach ($a in $addrs) {
    try {
        $s = Invoke-RestMethod -UseBasicParsing -TimeoutSec 3 -Uri "http://${a}:8080/status"
        if ($s.id -eq "@@SID@@") { $ip = $a; break }
    } catch { }
}
if (-not $ip) { $ip = $addrs[-1] }

$ca = Join-Path $env:TEMP "pes2021-ca.crt"
Invoke-WebRequest -UseBasicParsing -Uri "$PesBase/ca.crt" -OutFile $ca
Import-Certificate -FilePath $ca -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Remove-Item $ca

Add-Content -Path $HostsFile -Value ($Names | ForEach-Object { "$ip  $_  $Marker" }) -Encoding ASCII
ipconfig /flushdns | Out-Null

Write-Host ""
Write-Host "Done! Start PES 2021 -> eFootball -> Quick Match to play on $PesServer." -ForegroundColor Green
Write-Host "If Windows Firewall asks about PES 2021, click Allow."
Write-Host "Undo any time:  irm $PesBase/leave | iex"
