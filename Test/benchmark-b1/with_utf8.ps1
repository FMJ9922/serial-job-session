param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PythonArguments
)

$previousUtf8 = $env:PYTHONUTF8
$previousIoEncoding = $env:PYTHONIOENCODING
$desktopAssociationNames = @('CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID')
$previousDesktopAssociation = @{}
foreach ($associationName in $desktopAssociationNames) {
    $previousDesktopAssociation[$associationName] = [Environment]::GetEnvironmentVariable($associationName, 'Process')
}
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    foreach ($associationName in $desktopAssociationNames) {
        [Environment]::SetEnvironmentVariable($associationName, $null, 'Process')
    }
    & python @PythonArguments
    $pythonExitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONUTF8 = $previousUtf8
    $env:PYTHONIOENCODING = $previousIoEncoding
    foreach ($associationName in $desktopAssociationNames) {
        [Environment]::SetEnvironmentVariable($associationName, $previousDesktopAssociation[$associationName], 'Process')
    }
}
exit $pythonExitCode
