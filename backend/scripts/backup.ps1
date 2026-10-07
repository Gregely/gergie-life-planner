# Back up the meal planner database on Windows.
#
#   .\scripts\backup.ps1                 # from the backend folder
#   .\scripts\backup.ps1 -Keep 14
#   .\scripts\backup.ps1 -Db C:\data\mealplanner.db -Dest D:\backups
#
# Uses the project's .venv Python if present, otherwise `py -3` / `python`.
# Safe to run while the app is running. Schedule it with Task Scheduler (see README).
param(
    [string]$Db,
    [string]$Dest,
    [int]$Keep = 30
)
$ErrorActionPreference = 'Stop'
$backend = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot 'backup.py'

$venvPython = Join-Path $backend '.venv\Scripts\python.exe'
if (Test-Path $venvPython) { $python = @($venvPython) }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = @('py', '-3') }
else { $python = @('python') }

$argsList = @($script, '--keep', $Keep)
if ($Db) { $argsList += @('--db', $Db) }
if ($Dest) { $argsList += @('--dest', $Dest) }

$exe = $python[0]
$pre = if ($python.Count -gt 1) { $python[1..($python.Count - 1)] } else { @() }
& $exe @pre @argsList
exit $LASTEXITCODE
