param(
    [string]$MissionPlannerDir = "${env:ProgramFiles(x86)}\Mission Planner",
    [switch]$SkipPluginBuild
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$mp = (Resolve-Path -LiteralPath $MissionPlannerDir).Path
$build = Join-Path $repo 'build'
$payload = Join-Path $build 'plugin'
New-Item -ItemType Directory -Force $payload | Out-Null
if (-not $SkipPluginBuild) {
    & dotnet build (Join-Path $repo 'src\sender\DroneVoicePlugin.csproj') -c Release "-p:MissionPlannerDir=$mp" -o $payload
    if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed' }
}
$names = @('DroneVoicePlugin.dll','NAudio.Core.dll','NAudio.WinMM.dll','Concentus.dll','Microsoft.Win32.Registry.dll','System.Security.AccessControl.dll','System.Security.Principal.Windows.dll')
$source = [IO.File]::ReadAllText((Join-Path $repo 'src\windows-installer\Setup.cs'))
$entries = foreach ($name in $names) {
    $file = Join-Path $payload $name
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing payload: $name" }
    '{"' + $name + '", "' + (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash + '"}'
}
$start = $source.IndexOf('public static readonly Dictionary<string,string> Expected')
$end = $source.IndexOf('};', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Installer template hash table not found' }
$source = $source.Substring(0,$start) + "public static readonly Dictionary<string,string> Expected = new Dictionary<string,string> {`n" + ($entries -join ",`n") + "`n    };" + $source.Substring($end+2)
$hostVersion = [Reflection.AssemblyName]::GetAssemblyName((Join-Path $mp 'MissionPlanner.exe')).Version.ToString()
$source = $source.Replace('1.3.9384.38250',$hostVersion)
$generated = Join-Path $build 'Setup.generated.cs'
[IO.File]::WriteAllText($generated,$source,[Text.UTF8Encoding]::new($true))
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
$options = @('/nologo','/platform:anycpu','/optimize+','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Xml.Linq.dll')
foreach ($name in $names) { $options += '/resource:"' + (Join-Path $payload $name) + '",Payload.' + $name }
$options += '/resource:"' + (Join-Path $repo 'src\windows-installer\THIRD-PARTY-NOTICES.txt') + '",Payload.THIRD-PARTY-NOTICES.txt'
$release = @('/target:winexe',('/out:"' + (Join-Path $build 'DroneVoice_MissionPlanner_Setup.exe') + '"'),('/win32manifest:"' + (Join-Path $repo 'src\windows-installer\setup.manifest') + '"')) + $options + @('"'+$generated+'"')
$test = @('/target:exe','/define:TEST_BUILD',('/out:"'+(Join-Path $build 'SetupTest.exe')+'"')) + $options + @('"'+$generated+'"')
[IO.File]::WriteAllLines((Join-Path $build 'release.rsp'),$release,[Text.UTF8Encoding]::new($true))
[IO.File]::WriteAllLines((Join-Path $build 'test.rsp'),$test,[Text.UTF8Encoding]::new($true))
& $csc "@$(Join-Path $build 'release.rsp')"
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
& $csc "@$(Join-Path $build 'test.rsp')"
if ($LASTEXITCODE -ne 0) { throw 'Test installer build failed' }
Write-Output "Built plugin and installer for Mission Planner $hostVersion in $build"
