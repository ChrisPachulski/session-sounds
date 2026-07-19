Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repository = 'ChrisPachulski/session-sounds'

function Throw-InstallerError {
    param([Parameter(Mandatory)][string]$Message)
    throw "session-sounds installer: $Message"
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Throw-InstallerError "required command '$Name' was not found"
    }
}

function Get-PluginVersion {
    param([Parameter(Mandatory)][string]$CargoManifest)
    $Text = Get-Content -LiteralPath $CargoManifest -Raw
    if ($Text -notmatch '(?ms)^\[package\]\s*$(.*?)(?=^\[|\z)') {
        Throw-InstallerError "could not find [package] in '$CargoManifest'"
    }
    $Package = $Matches[1]
    if ($Package -notmatch '(?m)^\s*version\s*=\s*"([^"]+)"\s*$') {
        Throw-InstallerError "could not read package.version from '$CargoManifest'"
    }
    $Version = $Matches[1]
    if ($Version.StartsWith('v')) {
        Throw-InstallerError "Cargo.toml contains an invalid release version '$Version'"
    }
    return $Version
}

function Resolve-ReleaseTarget {
    $Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    if ($Architecture -ne 'AMD64') {
        Throw-InstallerError "unsupported Windows architecture '$Architecture'; only AMD64/x86_64 is supported"
    }
    return 'x86_64-pc-windows-msvc'
}

function Invoke-DownloadWithRetry {
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][string]$OutFile
    )
    $LastError = $null
    foreach ($Attempt in 1..3) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $OutFile
            return
        } catch {
            $LastError = $_
            if ($Attempt -lt 3) {
                Start-Sleep -Seconds $Attempt
            }
        }
    }
    Throw-InstallerError "download failed after 3 attempts: $Uri ($LastError)"
}

function Get-ExpectedChecksum {
    param(
        [Parameter(Mandatory)][string]$ChecksumFile,
        [Parameter(Mandatory)][string]$AssetName
    )
    $Candidates = @()
    foreach ($Line in Get-Content -LiteralPath $ChecksumFile) {
        if ($Line -match '^([0-9A-Fa-f]{64})\s+\*?(.+)$' -and $Matches[2] -ceq $AssetName) {
            $Candidates += $Matches[1].ToLowerInvariant()
        }
    }
    if ($Candidates.Count -ne 1) {
        Throw-InstallerError "SHA256SUMS has no single valid checksum for '$AssetName'"
    }
    return $Candidates[0]
}

function Confirm-ArchiveChecksum {
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$ExpectedHash
    )
    $ActualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -cne $ExpectedHash) {
        Throw-InstallerError "checksum mismatch for '$(Split-Path -Leaf $ArchivePath)'; the existing binary was not changed"
    }
}

function Install-Binary {
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$StageDirectory,
        [Parameter(Mandatory)][string]$Destination
    )
    $ExtractDirectory = Join-Path $StageDirectory 'extracted'
    New-Item -ItemType Directory -Path $ExtractDirectory | Out-Null
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractDirectory
    $ExtractedBinary = Join-Path $ExtractDirectory 'session-sounds.exe'
    if (-not (Test-Path -LiteralPath $ExtractedBinary -PathType Leaf)) {
        Throw-InstallerError "archive did not contain a session-sounds.exe binary"
    }
    $Prepared = Join-Path $StageDirectory 'session-sounds.prepared.exe'
    [System.IO.File]::Move($ExtractedBinary, $Prepared)
    if (Test-Path -LiteralPath $Destination) {
        if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
            Throw-InstallerError "install destination '$Destination' is not a regular file"
        }
        $Backup = Join-Path $StageDirectory 'session-sounds.backup.exe'
        [System.IO.File]::Replace($Prepared, $Destination, $Backup, $true)
        Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
    } else {
        [System.IO.File]::Move($Prepared, $Destination)
    }
}

function Install-SessionSounds {
    Assert-CommandAvailable 'Invoke-WebRequest'
    Assert-CommandAvailable 'Expand-Archive'
    Assert-CommandAvailable 'Get-FileHash'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $PluginRoot = Split-Path -Parent $PSScriptRoot
    $Version = Get-PluginVersion (Join-Path $PluginRoot 'Cargo.toml')
    $Target = Resolve-ReleaseTarget
    $Asset = "session-sounds-v$Version-$Target.zip"
    $BaseUrl = "https://github.com/$Repository/releases/download/v$Version"
    $BinDirectory = Join-Path $PluginRoot 'bin'
    New-Item -ItemType Directory -Path $BinDirectory -Force | Out-Null
    $StageDirectory = Join-Path $BinDirectory ('.session-sounds-install-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $StageDirectory | Out-Null
    try {
        $Archive = Join-Path $StageDirectory $Asset
        $Checksums = Join-Path $StageDirectory 'SHA256SUMS'
        Invoke-DownloadWithRetry "$BaseUrl/$Asset" $Archive
        Invoke-DownloadWithRetry "$BaseUrl/SHA256SUMS" $Checksums
        $ExpectedHash = Get-ExpectedChecksum $Checksums $Asset
        Confirm-ArchiveChecksum -ArchivePath $Archive -ExpectedHash $ExpectedHash
        Install-Binary -ArchivePath $Archive -StageDirectory $StageDirectory -Destination (Join-Path $BinDirectory 'session-sounds.exe')
        Write-Host "Installed Session Sounds $Version for $Target."
    } finally {
        Remove-Item -LiteralPath $StageDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Install-SessionSounds
}
