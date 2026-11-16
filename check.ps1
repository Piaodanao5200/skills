<#
  check.ps1 —— 检查实体与链接是否一致、SKILL.md 是否合规。

  检查四类问题：
    1. 实体缺 SKILL.md、name/description 缺失、目录名与 name 不一致
    2. 链接断链（实体已被删除或移动）
    3. 链接指向的不是本仓库（孤儿链接）
    4. 某个已安装的 agent 漏挂（只提示，不算错误）

  退出码：0 无错误 · 1 有错误
#>
[CmdletBinding()]
param(
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

$TARGETS = [ordered]@{
  'WorkBuddy' = Join-Path $env:USERPROFILE '.workbuddy\skills'
  'Agents'    = Join-Path $env:USERPROFILE '.agents\skills'
  'Codex'     = Join-Path $env:USERPROFILE '.codex\skills'
  'CodeBuddy' = Join-Path $env:USERPROFILE '.codebuddy\skills'
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

function Read-Frontmatter([string]$md) {
  $raw = Get-Content -LiteralPath $md -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return $null }
  $raw = $raw.TrimStart([char]0xFEFF)
  $m = [regex]::Match($raw, '(?s)\A---\r?\n(.*?)\r?\n---')
  if (-not $m.Success) { return $null }
  $body = $m.Groups[1].Value
  $name = ''
  $desc = ''
  $nm = [regex]::Match($body, '(?m)^name:\s*(.+?)\s*$')
  if ($nm.Success) { $name = $nm.Groups[1].Value.Trim('"', "'") }
  $dm = [regex]::Match($body, '(?m)^description:\s*(.+?)\s*$')
  if ($dm.Success) { $desc = $dm.Groups[1].Value.Trim('"', "'") }
  return [pscustomobject]@{ Name = $name; Desc = $desc }
}

$errors = New-Object System.Collections.ArrayList
$warns = New-Object System.Collections.ArrayList
$rootNorm = Norm($Root)

Write-Host ''
Write-Host 'SKILL 校验' -ForegroundColor Cyan
Write-Host ("实体目录  " + $Root) -ForegroundColor DarkGray
Write-Host ''

# ---- 1. 实体侧 ----
$dirs = @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -notmatch '^[._]'
} | Sort-Object Name)

$valid = @{}
Write-Host ('[1] 实体 ' + $dirs.Count + ' 个目录') -ForegroundColor White
foreach ($d in $dirs) {
  $md = Join-Path $d.FullName 'SKILL.md'
  if (-not (Test-Path -LiteralPath $md)) {
    [void]$errors.Add("$($d.Name): 缺 SKILL.md，不会被加载")
    Write-Host ("    {0,-24} ERR 缺 SKILL.md" -f $d.Name) -ForegroundColor Red
    continue
  }
  $fm = Read-Frontmatter $md
  if ($null -eq $fm) {
    [void]$errors.Add("$($d.Name): SKILL.md 开头没有 --- 包裹的 YAML frontmatter")
    Write-Host ("    {0,-24} ERR 无 frontmatter" -f $d.Name) -ForegroundColor Red
    continue
  }
  $bad = @()
  if ([string]::IsNullOrWhiteSpace($fm.Name)) { $bad += '缺 name' }
  if ([string]::IsNullOrWhiteSpace($fm.Desc)) { $bad += '缺 description' }
  if ($fm.Name -and ($fm.Name -ne $d.Name)) { $bad += ("name 为 '$($fm.Name)'，与目录名不一致") }

  if ($bad.Count -gt 0) {
    foreach ($b in $bad) { [void]$errors.Add("$($d.Name): $b") }
    Write-Host ("    {0,-24} ERR {1}" -f $d.Name, ($bad -join '; ')) -ForegroundColor Red
  } else {
    $valid[$d.Name] = $d.FullName
    Write-Host ("    {0,-24} OK" -f $d.Name) -ForegroundColor Green
  }
}

# ---- 2. 链接侧 ----
Write-Host ''
Write-Host '[2] 链接' -ForegroundColor White
$linkMap = @{}

foreach ($k in $TARGETS.Keys) {
  $dir = $TARGETS[$k]
  if (-not (Test-Path -LiteralPath $dir)) { continue }
  $entries = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | Where-Object {
    $_.LinkType -eq 'Junction' -or $_.LinkType -eq 'SymbolicLink'
  })
  foreach ($e in $entries) {
    $t = $e.Target
    if ($t -is [array]) { $t = $t[0] }
    $tn = Norm($t)

    if (-not ($rootNorm -and $tn.StartsWith($rootNorm))) { continue }   # 别的来源，不管

    if (-not (Test-Path -LiteralPath $t)) {
      [void]$errors.Add("$k\$($e.Name): 断链，目标 $t 不存在")
      Write-Host ("    {0}\{1,-20} ERR 断链 -> {2}" -f $k, $e.Name, $t) -ForegroundColor Red
      continue
    }
    if (-not (Test-Path -LiteralPath (Join-Path $t 'SKILL.md'))) {
      [void]$errors.Add("$k\$($e.Name): 目标目录里没有 SKILL.md")
      Write-Host ("    {0}\{1,-20} ERR 目标缺 SKILL.md" -f $k, $e.Name) -ForegroundColor Red
      continue
    }
    if (-not $linkMap.ContainsKey($e.Name)) { $linkMap[$e.Name] = @() }
    $linkMap[$e.Name] += $k
  }
}

# 正向检查：每个合规实体是否都已挂到每个已安装 agent
$installed = @()
foreach ($k in $TARGETS.Keys) {
  if (Test-Path -LiteralPath (Split-Path -Path $TARGETS[$k] -Parent)) { $installed += $k }
}

$missingAny = $false
foreach ($name in $valid.Keys) {
  $has = @()
  if ($linkMap.ContainsKey($name)) { $has = $linkMap[$name] }
  $missing = @($installed | Where-Object { $has -notcontains $_ })
  if ($missing.Count -eq 0) {
    Write-Host ("    {0,-24} OK 已挂 {1}" -f $name, ($has -join ', ')) -ForegroundColor Green
  } else {
    $missingAny = $true
    [void]$warns.Add("$name`: 未挂到 $($missing -join ', ')")
    $extra = ''
    if ($has.Count -gt 0) { $extra = " · 已挂 " + ($has -join ', ') }
    Write-Host ("    {0,-24} WARN 未挂 {1}{2}" -f $name, ($missing -join ', '), $extra) -ForegroundColor Yellow
  }
}

# 反向检查：链接存在但实体已不在本仓库
foreach ($name in $linkMap.Keys) {
  if (-not $valid.ContainsKey($name)) {
    [void]$errors.Add("$name`: 有链接但实体目录不存在或不合规")
  }
}

# ---- 3. 汇总 ----
Write-Host ''
if ($errors.Count -eq 0 -and $warns.Count -eq 0) {
  Write-Host ("全部正常 · 实体 {0} 个 · 已安装 agent {1} 个" -f $valid.Count, $installed.Count) -ForegroundColor Green
} elseif ($errors.Count -eq 0) {
  Write-Host ("通过 · 实体 {0} 个 · 警告 {1} 条" -f $valid.Count, $warns.Count) -ForegroundColor Yellow
  if (-not $Quiet) { foreach ($w in $warns) { Write-Host ("    - " + $w) -ForegroundColor DarkGray } }
  Write-Host '漏挂的直接跑 挂载.cmd 即可补齐。' -ForegroundColor DarkGray
} else {
  Write-Host ("失败 · 错误 {0} 条 · 警告 {1} 条" -f $errors.Count, $warns.Count) -ForegroundColor Red
  if (-not $Quiet) { foreach ($e in $errors) { Write-Host ("    - " + $e) -ForegroundColor DarkGray } }
}
Write-Host ''
Write-Host ("已安装 agent: " + ($installed -join ', ')) -ForegroundColor DarkGray

if ($errors.Count -gt 0) { exit 1 }
exit 0
