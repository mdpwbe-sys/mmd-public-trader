[CmdletBinding()]
param(
    [string]$FilePath = (Join-Path $PSScriptRoot 'dist\MMD-Trader.exe'),
    [string]$ExpectedVersion,
    [string]$ExpectedSha256,
    [switch]$LaunchSmokeTest
)

$ErrorActionPreference = 'Stop'
$file = Get-Item -LiteralPath $FilePath -ErrorAction Stop
$hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
$version = $file.VersionInfo

if (-not $ExpectedVersion) {
    $versionSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'version.py') -Raw
    $match = [regex]::Match($versionSource, '(?m)^VERSION\s*=\s*["'']([^"'']+)["'']')
    if ($match.Success) { $ExpectedVersion = $match.Groups[1].Value }
}
if ($ExpectedVersion -and $version.ProductVersion -notlike "$ExpectedVersion*") {
    throw "ProductVersion '$($version.ProductVersion)' does not match version.py '$ExpectedVersion'."
}

$sidecar = "$($file.FullName).sha256"
if (Test-Path -LiteralPath $sidecar) {
    $sidecarHash = ([regex]::Match((Get-Content -LiteralPath $sidecar -Raw), '[A-Fa-f0-9]{64}')).Value.ToLowerInvariant()
    if ($sidecarHash -ne $hash) { throw 'The EXE changed after the recorded signature hash.' }
}
if ($ExpectedSha256 -and $ExpectedSha256.ToLowerInvariant() -ne $hash) {
    throw 'The EXE SHA256 does not match ExpectedSha256.'
}

[pscustomobject]@{
    File = $file.FullName
    SizeBytes = $file.Length
    SHA256 = $hash
    FileVersion = $version.FileVersion
    ProductVersion = $version.ProductVersion
    AuthenticodeStatus = $signature.Status
    SignatureSubject = $signature.SignerCertificate.Subject
    HashMatchesSignedArtifact = if (Test-Path -LiteralPath $sidecar) { $true } else { 'No sidecar yet' }
} | Format-List

if ($LaunchSmokeTest) {
    $process = Start-Process -FilePath $file.FullName -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 4
    if ($process.HasExited) { throw "Launch smoke test failed with exit code $($process.ExitCode)." }
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    Write-Host 'Launch smoke test: process started successfully.'
}

if ($signature.Status -notin @('Valid', 'NotSigned')) {
    throw "Unexpected Authenticode status: $($signature.Status)"
}
