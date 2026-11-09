$ErrorActionPreference = 'Stop'
$bridgeDesktop = [Environment]::GetFolderPath('Desktop')
$bridgeShell = New-Object -ComObject WScript.Shell
$bridgeLink = $bridgeShell.CreateShortcut((Join-Path $bridgeDesktop 'Context Bridge.lnk'))
$bridgeLink.TargetPath = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$bridgeLink.Arguments = '-NoProfile -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'Open-ContextBridgeUI.ps1') + '"'
$bridgeLink.WorkingDirectory = $PSScriptRoot
$bridgeLink.WindowStyle = 7
$bridgeLink.Description = 'Open the local Context Bridge handoff library'
$bridgeIcon = Join-Path $PSScriptRoot 'contextrail/ui_static/bridge-icon.ico'
if (Test-Path -LiteralPath $bridgeIcon) { $bridgeLink.IconLocation = $bridgeIcon + ',0' }
else { $bridgeLink.IconLocation = (Join-Path $env:SystemRoot 'System32/shell32.dll') + ',22' }
$bridgeLink.Save()
Write-Output (Join-Path $bridgeDesktop 'Context Bridge.lnk')
