<#
  unmount.ps1 —— 删掉 agent 目录里指向本仓库的链接。实体文件一个都不动。

  用法：
    .\unmount.ps1                       卸掉所有 agent、所有 skill
    .\unmount.ps1 -Only WorkBuddy       只卸 WorkBuddy
    .\unmount.ps1 -Skill my-skill-a     只卸一个 skill
    .\unmount.ps1 -DryRun               只看计划

  安全说明：只用 Directory.Delete(path, false) 删除 reparse point 本身。
  绝不用 Remove-Item -Recurse，那会顺着链接把实体内容删掉。
#>
[CmdletBinding()]
param(
  [string[]]$Only,
  [string[]]$Skill,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

$TARGETS = [ordered]@{
  'WorkBuddy' = Join-Path $env:USERPROFILE '.workbuddy\skills'
  'Agents'    = Join-Path $env:USERPROFILE '.agents\skills'
  'Codex'     = Join-Path $env:USERPROFILE '.codex\skills'
  'CodeBuddy' = Join-Path $env:USERPROFILE '.codebuddy\skills'
  'Doubao'    = Join-Path $env:LOCALAPPDATA 'Doubao\User Data\Default\.doubao\agent_mode\workspace\.user_skills'
  'Claude'    = Join-Path $env:USERPROFILE '.claude\skills'
  'Cursor'    = Join-Path $env:USERPROFILE '.cursor\skills'
}

function Norm([string]$p) {
  if ([string]::IsNullOrWhiteSpace($p)) { return '' }
  return [System.IO.Path]::GetFullPath($p).TrimEnd('\').ToLowerInvariant()
}

function Get-JunctionTarget([string]$p) {
  $i = Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
  if ($null -eq $i) { return $null }
  if (-not $i.LinkType) { return $null }
  $t = $i.Target
  if ($t -is [array]) { $t = $t[0] }
  return $t
}

Write-Host ''
Write-Host 'SKILL 卸载' -ForegroundColor Cyan
Write-Host ("实体目录  " + $Root + "（不会被动）") -ForegroundColor DarkGray
if ($DryRun) { Write-Host '模式      DRY RUN' -ForegroundColor Yellow }
Write-Host ''

$removed = 0
$kept = 0
$rootNorm = Norm($Root)

foreach ($k in $TARGETS.Keys) {
  if ($Only -and ($Only -notcontains $k)) { continue }
  $dir = $TARGETS[$k]
  if (-not (Test-Path -LiteralPath $dir)) { continue }

  $entries = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | Where-Object {
    $_.LinkType -eq 'Junction' -or $_.LinkType -eq 'SymbolicLink'
  })
  if ($entries.Count -eq 0) { continue }

  Write-Host $k -ForegroundColor White
  foreach ($e in $entries) {
    if ($Skill -and ($Skill -notcontains $e.Name)) { continue }

    $t = $e.Target
    if ($t -is [array]) { $t = $t[0] }
    # 只处理指向本仓库的链接，别的不碰
    if (-not ($rootNorm -and (Norm($t)).StartsWith($rootNorm))) {
      Write-Host ("    {0,-20} 不属于本仓库，跳过" -f $e.Name) -ForegroundColor DarkGray
      $kept++
      continue
    }

    if ($DryRun) {
      Write-Host ("    {0,-20} 将删除链接" -f $e.Name) -ForegroundColor Yellow
      $removed++
      continue
    }
    try {
      [System.IO.Directory]::Delete($e.FullName, $false)
      Write-Host ("    {0,-20} 已解除" -f $e.Name) -ForegroundColor Green
      $removed++
    } catch {
      Write-Host ("    {0,-20} [失败] {1}" -f $e.Name, $_.Exception.Message) -ForegroundColor Red
    }
  }
}

Write-Host ''
Write-Host ("解除 {0} 个链接 · 保留 {1} 个（非本仓库）" -f $removed, $kept) -ForegroundColor Cyan
Write-Host '实体文件未做任何改动。' -ForegroundColor DarkGray
Write-Host ''
