# ContextRail host adapter: parse literal Get-Content, never invoke the supplied command.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$tokens = $null
$errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseInput([string]$request.command, [ref]$tokens, [ref]$errors)
$paths = @()
$commands = @($tree.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
if ($errors.Count -eq 0 -and $commands.Count -eq 1 -and $tree.EndBlock.Statements.Count -eq 1) {
    $statement = $tree.EndBlock.Statements[0]
    $command = $commands[0]
    if ($statement -is [System.Management.Automation.Language.PipelineAst] -and $statement.PipelineElements.Count -eq 1 -and $command.GetCommandName() -in @('Get-Content','Microsoft.PowerShell.Management\Get-Content') -and $command.Redirections.Count -eq 0) {
        $allowed = $true
        $expectPath = $false
        $expectCount = $false
        foreach ($element in @($command.CommandElements | Select-Object -Skip 1)) {
            if ($element -is [System.Management.Automation.Language.CommandParameterAst]) {
                if ($expectPath -or $expectCount) { $allowed = $false }
                if ($element.ParameterName -in @('LiteralPath','Path')) { $expectPath = $true }
                elseif ($element.ParameterName -in @('TotalCount','Head','Tail')) { $expectCount = $true }
                elseif ($element.ParameterName -notin @('Raw')) { $allowed = $false }
                if ($element.Argument) { $allowed = $false }
            } elseif ($element -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
                if (-not $expectCount -and ($expectPath -or $paths.Count -eq 0)) { $paths += [string]$element.Value; $expectPath = $false }
                else { $allowed = $false }
            } elseif ($expectCount -and $element -is [System.Management.Automation.Language.ConstantExpressionAst] -and $element.Value -is [int] -and $element.Value -ge 0) {
                $expectCount = $false
            } else { $allowed = $false }
        }
        if (-not $allowed -or $expectPath -or $expectCount) { $paths = @() }
    }
}
ConvertTo-Json -Compress -InputObject @{ paths = @($paths) }
