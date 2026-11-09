param([int]$Port = 37778, [int]$IdleTimeout = 600)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Open-ContextRailUI.ps1') -Port $Port -IdleTimeout $IdleTimeout -Page '/bridge'
