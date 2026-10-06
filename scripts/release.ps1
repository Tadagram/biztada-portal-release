<#
.SYNOPSIS
    Tadagram Worker Portal Linux - Release & Packaging Utility for Windows / PowerShell
.DESCRIPTION
    Automates version bumping, tar.gz bundle creation, and GitHub Release deployment.
#>
param (
    [string]$NewVersion
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Resolve-Path "$ScriptDir\.."

$VersionFile = Join-Path $BaseDir "VERSION"
$CurrentVersion = (Get-Content $VersionFile -Raw).Trim()

if (-not $NewVersion) {
    Write-Host "Current Worker Portal Linux Version: $CurrentVersion" -ForegroundColor Cyan
    $NewVersion = Read-Host "Enter new version (e.g. 1.0.1)"
}

if (-not $NewVersion) {
    Write-Error "Version cannot be empty."
}

Write-Host "Bumping version $CurrentVersion -> $NewVersion..." -ForegroundColor Green
$NewVersion | Set-Content $VersionFile -NoNewline

$ArchiveName = "tadagram-worker-portal-linux-v$NewVersion.tar.gz"
$ArchivePath = Join-Path $BaseDir $ArchiveName

Write-Host "Packaging $ArchiveName..." -ForegroundColor Cyan
if (Get-Command tar -ErrorAction SilentlyContinue) {
    tar -czf $ArchivePath --exclude=".git" --exclude="data/workers/*" --exclude="data/shared/*" --exclude="*.tar.gz" -C $BaseDir .
    Write-Host "Successfully generated $ArchiveName" -ForegroundColor Green
} else {
    Write-Warning "tar command not found in PATH. Please install tar or build on Linux."
}

Write-Host "`n=== Deployment Instructions ===" -ForegroundColor Yellow
Write-Host "1. git add workers/worker-portal-linux"
Write-Host "2. git commit -m 'release(portal-linux): v$NewVersion'"
Write-Host "3. git tag -a v$NewVersion -m 'Release v$NewVersion'"
Write-Host "4. git push origin v$NewVersion"
Write-Host "5. gh release create v$NewVersion $ArchivePath --title 'Worker Portal Linux v$NewVersion' --notes-file CHANGELOG.md"
