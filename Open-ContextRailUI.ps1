param([int]$Port = 37778, [int]$IdleTimeout = 600, [ValidateSet('/rail','/bridge','/viewer','/')][string]$Page = '/')
$ErrorActionPreference = 'Stop'
$railRoot = $PSScriptRoot
$activation = Get-Content -LiteralPath (Join-Path $railRoot '.context-rail/active-native-client.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$registryPath = $activation.layout.registry
if (-not $registryPath) { throw '请先完成现有原生客户端接线，界面读取该项目的确切 registry。' }
$railPython = Join-Path $railRoot '.venv/Scripts/python.exe'
$railArguments = @('-m', 'contextrail.ui_server', '--registry', ('"' + $registryPath + '"'), '--port', $Port, '--idle-timeout', $IdleTimeout, '--open')
if ($Page -ne '/') { $railArguments += @('--page', $Page) }
Start-Process -FilePath $railPython -ArgumentList $railArguments -WorkingDirectory $railRoot -WindowStyle Hidden
