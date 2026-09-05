[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$file = (Resolve-Path -LiteralPath $FilePath).Path
$signTool = Get-Command signtool.exe -ErrorAction Stop

# The certificate must already be available in the Windows certificate store.
# No private key, PFX, password or signing token is stored in this repository.
& $signTool.Source sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $file
if ($LASTEXITCODE -ne 0) { throw "SignTool failed with exit code $LASTEXITCODE." }

$signature = Get-AuthenticodeSignature -LiteralPath $file
if ($signature.Status -ne 'Valid') {
    throw "Signature verification failed: $($signature.Status) $($signature.StatusMessage)"
}

$hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
$sidecar = "$file.sha256"
Set-Content -LiteralPath $sidecar -Value "$hash *$(Split-Path -Leaf $file)" -NoNewline -Encoding ascii
Write-Host "Signed and verified: $file"
Write-Host "SHA256 sidecar: $sidecar"
