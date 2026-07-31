<#
  rr-assistant-smoke-fixture.ps1
  Red Robin - AI-assistant operator-smoke fixture (reactive grounded recommendation).

  Plants a tiny, OBVIOUSLY-FAKE loot surface so the smoke runs with ZERO
  victim-machine config: no admin, no new users, no privileges, no AD. It is a
  small folder of decoy text files, fully removed by -Cleanup.

  The scenario is a recon -> loot -> follow-the-hint chain:
    1. db_backup.config  - a FAKE service credential (svc_reports / SQL01)
    2. notes_old.txt     - a note pointing at an old file server share
    3. exports\svc_sql_readme.txt - the "followed hint": a second FAKE finding
       (svc_sql, described as broadly privileged) that the note leads to.

  All credentials are fake, for servers that do not exist. Safe on any lab
  Windows box you control.

  Run (normal PowerShell, no admin needed):
    powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1
    powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup
#>
[CmdletBinding()]
param(
  [switch]$Cleanup,
  [string]$Path
)

# No-admin, ideally cross-user-readable location: C:\Users\Public\RR-Smoke.
if (-not $Path) { $Path = Join-Path $env:PUBLIC 'RR-Smoke' }
$Fallback = Join-Path $env:USERPROFILE 'Documents\RR-Smoke'

if ($Cleanup) {
  foreach ($p in @($Path, $Fallback)) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "[rr-smoke] removed $p" }
  }
  return
}

# Create the fixture dir; fall back to the user's Documents if Public is not writable.
try {
  New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
  $Root = $Path
} catch {
  New-Item -ItemType Directory -Path $Fallback -Force | Out-Null
  $Root = $Fallback
}
$Exports = Join-Path $Root 'exports'
New-Item -ItemType Directory -Path $Exports -Force | Out-Null

# Decoy 1 - a "DB backup" config carrying a FAKE service credential.
@'
# weekly DB backup job  (RR-SMOKE FIXTURE - FAKE, not real loot)
[connection]
Server   = SQL01.corp.local
Database = ReportsDW
User Id  = svc_reports
Password = R3p0rts-B@ckup!2023
'@ | Set-Content -LiteralPath (Join-Path $Root 'db_backup.config') -Encoding UTF8

# Decoy 2 - a stray note hinting at a share (a 2nd finding for the enricher, and
# the breadcrumb the operator follows to decoy 3).
@'
TODO: migrate the weekly export off the old file server.
  \\FILES01\Finance\exports\   (svc_reports has read there; see exports\svc_sql_readme.txt)
'@ | Set-Content -LiteralPath (Join-Path $Root 'notes_old.txt') -Encoding UTF8

# Decoy 3 - the "followed hint": a second, higher-value FAKE finding the note
# points to (svc_sql, described as broadly privileged -> a path-to-DA breadcrumb).
@'
readme for the migrated exports  (RR-SMOKE FIXTURE - FAKE, not real loot)
The export job runs as CORP\svc_sql. That account is a member of the SQL admins
group and is used across most of the estate. Old creds were:
  CORP\svc_sql : S3rv3r-Sql!Adm2022
'@ | Set-Content -LiteralPath (Join-Path $Exports 'svc_sql_readme.txt') -Encoding UTF8

Write-Host ""
Write-Host "[rr-smoke] fixture planted at: $Root" -ForegroundColor Green
Write-Host ""
Write-Host "From your beacon (normal user - no privesc). Two recon steps:" -ForegroundColor Cyan
Write-Host "  1) loot the config:" -ForegroundColor Cyan
Write-Host "       shell dir  `"$Root`""
Write-Host "       shell type `"$Root\db_backup.config`""
Write-Host "       shell type `"$Root\notes_old.txt`""
Write-Host "  2) follow the hint the note points to:" -ForegroundColor Cyan
Write-Host "       shell dir  `"$Exports`""
Write-Host "       shell type `"$Exports\svc_sql_readme.txt`""
Write-Host ""
Write-Host "Expect within a few seconds of each step: an advisory in the Chat pane (Red Robin · advisory)" -ForegroundColor Cyan
Write-Host "with the 5 parts (WHAT / why / opsec / conf / refs), refs citing the facts the"
Write-Host "enricher extracted (e.g. the svc_reports cred) + a KB chunk. Nothing is"
Write-Host "dispatched. See the runbook for checkpoints."
Write-Host ""
Write-Host "Cleanup when done:  powershell -ExecutionPolicy Bypass -File .\rr-assistant-smoke-fixture.ps1 -Cleanup" -ForegroundColor DarkGray
