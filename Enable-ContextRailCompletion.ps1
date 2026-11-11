param(
    [Parameter(Mandatory)][string]$Session,
    [string]$Workspace = (Get-Location).Path,
    [string]$Python = 'python'
)

$railWorkspace = (Resolve-Path -LiteralPath $Workspace).ProviderPath
$railSession = $Session
$railPython = $Python
$complete = {
    param($wordToComplete, $commandAst, $cursorPosition)

    $elements = @($commandAst.CommandElements | Where-Object { $_.Extent.StartOffset -lt $cursorPosition })
    if ($elements.Count -lt 3 -or $elements[1].Extent.Text -notin @('bookmark-open', 'bookmark-remove')) { return }
    $flagIndex = $elements.Count - 1
    if ($wordToComplete) { $flagIndex-- }
    if ($elements[$flagIndex].Extent.Text -ne '--name') { return }

    $prefix = $wordToComplete.Trim([char]39, [char]34)
    if ($prefix -and $prefix -notmatch '^\w[\w.-]{0,63}$') { return }
    $options = @{ '--workspace' = $railWorkspace; '--session' = $railSession }
    $bindingFlags = @('--workspace', '--session', '--config', '--defaults', '--store', '--project-id', '--tenant')
    for ($index = 2; $index -lt $flagIndex; $index++) {
        $flag = $elements[$index].Extent.Text
        if ($flag -in $bindingFlags) {
            $next = $elements[$index + 1]
            if ($next -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
                $next.Value.StartsWith('--')) { return }
            $options[$flag] = $next.Value
            $index++
        } elseif ($flag -like '--*=*' -and ($flag.Split('=', 2)[0] -in $bindingFlags)) {
            return
        }
    }
    $argv = @('-m', 'contextrail', 'bookmark-list', '--completion', '--limit', '30', "--prefix=$prefix")
    foreach ($option in $options.GetEnumerator()) {
        $argv += @($option.Key, $option.Value)
    }
    $raw = & $railPython @argv 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return }
    foreach ($name in (ConvertFrom-Json -InputObject ($raw -join [Environment]::NewLine))) {
        [System.Management.Automation.CompletionResult]::new($name, $name, 'ParameterValue', 'Saved ContextRail bookmark')
    }
}.GetNewClosure()

Register-ArgumentCompleter -Native -CommandName contextrail -ScriptBlock $complete
