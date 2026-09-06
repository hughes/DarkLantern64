param(
    [switch]$Run,
    [switch]$Autoplay,
    [switch]$Editor,
    [switch]$Test,
    [string]$Level = "",
    [string]$Bundle = "",
    [string]$StartLevel = "",
    [string]$StartPreset = "",
    [string]$Sdk = ""
)
$ErrorActionPreference = 'Stop'
$taskArguments = @("$PSScriptRoot/tools/build.py")
if ($Run) { $taskArguments += '--run' }
if ($Autoplay) { $taskArguments += '--autoplay' }
if ($Editor) { $taskArguments += '--editor' }
if ($Test) { $taskArguments += '--test' }
if ($Level) { $taskArguments += @('--level', $Level) }
if ($Bundle) { $taskArguments += @('--bundle', $Bundle) }
if ($StartLevel) { $taskArguments += @('--start-level', $StartLevel) }
if ($StartPreset) { $taskArguments += @('--start-preset', $StartPreset) }
if ($Sdk) { $taskArguments += @('--sdk', $Sdk) }
& python @taskArguments
exit $LASTEXITCODE
