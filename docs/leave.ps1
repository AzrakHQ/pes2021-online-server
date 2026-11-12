# PES 2021 Online - undo the join line on Windows, without needing the server.
#   irm https://azrakhq.github.io/pes2021-online-server/leave.ps1 | iex
# Removes the hosts-file lines and every "PES2021 Private Server CA" certificate. Asks for admin rights.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command",
        "irm https://azrakhq.github.io/pes2021-online-server/leave.ps1 | iex")
    return
}
$HostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
$kept = @(Get-Content $HostsFile | Where-Object { $_ -notmatch [regex]::Escape("# pes2021-private-server") })
Set-Content -Path $HostsFile -Value $kept -Encoding ASCII
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -eq "CN=PES2021 Private Server CA" } | Remove-Item
ipconfig /flushdns | Out-Null
Write-Host "Done. PES 2021 is back to normal on this PC." -ForegroundColor Green
