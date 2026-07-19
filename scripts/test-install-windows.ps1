Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'install-windows.ps1')

function Assert-Equal {
    param($Actual, $Expected, [string]$Label)
    if ($Actual -cne $Expected) {
        throw "$Label`: expected '$Expected', got '$Actual'"
    }
}

$PreviousArchitecture = $env:PROCESSOR_ARCHITECTURE
$PreviousWowArchitecture = $env:PROCESSOR_ARCHITEW6432
$Temporary = Join-Path ([IO.Path]::GetTempPath()) ('session-sounds-installer-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temporary | Out-Null
try {
    $env:PROCESSOR_ARCHITECTURE = 'AMD64'
    Remove-Item Env:PROCESSOR_ARCHITEW6432 -ErrorAction SilentlyContinue
    Assert-Equal (Resolve-ReleaseTarget) 'x86_64-pc-windows-msvc' 'target'

    $Checksums = Join-Path $Temporary 'SHA256SUMS'
    $Hash = 'a' * 64
    Set-Content -LiteralPath $Checksums -Value @(
        "$Hash  near-session-sounds.zip",
        "$Hash *session-sounds-v1.0.0-x86_64-pc-windows-msvc.zip"
    )
    Assert-Equal (Get-ExpectedChecksum $Checksums 'session-sounds-v1.0.0-x86_64-pc-windows-msvc.zip') $Hash 'checksum lookup'

    Add-Content -LiteralPath $Checksums -Value "$Hash  session-sounds-v1.0.0-x86_64-pc-windows-msvc.zip"
    $DuplicateRejected = $false
    try {
        Get-ExpectedChecksum $Checksums 'session-sounds-v1.0.0-x86_64-pc-windows-msvc.zip' | Out-Null
    } catch {
        $DuplicateRejected = $true
    }
    Assert-Equal $DuplicateRejected $true 'duplicate checksum rejection'

    $PayloadDirectory = Join-Path $Temporary 'payload'
    $ReplacementStage = Join-Path $Temporary 'replacement-stage'
    $DestinationDirectory = Join-Path $Temporary 'bin'
    New-Item -ItemType Directory -Path $PayloadDirectory, $ReplacementStage, $DestinationDirectory | Out-Null
    Set-Content -LiteralPath (Join-Path $PayloadDirectory 'session-sounds.exe') -Value 'new binary' -NoNewline
    $Archive = Join-Path $Temporary 'session-sounds.zip'
    Compress-Archive -LiteralPath (Join-Path $PayloadDirectory 'session-sounds.exe') -DestinationPath $Archive
    $ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Confirm-ArchiveChecksum -ArchivePath $Archive -ExpectedHash $ArchiveHash
    $Destination = Join-Path $DestinationDirectory 'session-sounds.exe'
    Set-Content -LiteralPath $Destination -Value 'working binary' -NoNewline
    Install-Binary -ArchivePath $Archive -StageDirectory $ReplacementStage -Destination $Destination
    Assert-Equal (Get-Content -LiteralPath $Destination -Raw) 'new binary' 'atomic replacement'

    $MismatchRejected = $false
    try {
        Confirm-ArchiveChecksum -ArchivePath $Archive -ExpectedHash ('0' * 64)
    } catch {
        $MismatchRejected = $true
    }
    Assert-Equal $MismatchRejected $true 'checksum mismatch rejection'
    Assert-Equal (Get-Content -LiteralPath $Destination -Raw) 'new binary' 'mismatch preservation'

    $env:PROCESSOR_ARCHITECTURE = 'ARM64'
    $UnsupportedRejected = $false
    try {
        Resolve-ReleaseTarget | Out-Null
    } catch {
        $UnsupportedRejected = $true
    }
    Assert-Equal $UnsupportedRejected $true 'unsupported architecture rejection'
} finally {
    $env:PROCESSOR_ARCHITECTURE = $PreviousArchitecture
    if ($null -eq $PreviousWowArchitecture) {
        Remove-Item Env:PROCESSOR_ARCHITEW6432 -ErrorAction SilentlyContinue
    } else {
        $env:PROCESSOR_ARCHITEW6432 = $PreviousWowArchitecture
    }
    Remove-Item -LiteralPath $Temporary -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'Windows installer helpers: ok'
