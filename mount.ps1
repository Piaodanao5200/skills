<#
  mount.ps1 —— 把本目录下每个 skill 以 Junction 形式挂到各 agent 的 skills 目录。

  用法：
    .\mount.ps1                     挂到本机所有已安装的 agent
    .\mount.ps1 -Only WorkBuddy     只挂 WorkBuddy
    .\mount.ps1 -DryRun             只打印计划，不改动文件
    .\mount.ps1 -Force              目标已存在且指向别处时，删掉重建

  为什么用 Junction：本机未开开发者模式，SymbolicLink 需管理员权限，Junction 不需要。
#>
[CmdletBinding()]
param(
  [string[]]$Only,
  [switch]$Force,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

# 各 agent 的 skills 目录。父目录存在即视为已安装该 agent。
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

Write-Host ''
Write-Host 'SKILL 挂载' -ForegroundColor Cyan
Write-Host ("实体目录  " + $Root) -ForegroundColor DarkGray
if ($DryRun) { Write-Host '模式      DRY RUN（不改动任何文件）' -ForegroundColor Yellow }
Write-Host ''

# ---- 1. 找出已安装的 agent ----
$active = @()
$absent = @()
foreach ($k in $TARGETS.Keys) {
  if ($Only -and ($Only -notcontains $k)) { continue }
  $dir = $TARGETS[$k]
  $parent = Split-Path -Path $dir -Parent
  if (Test-Path -LiteralPath $parent) {
    $active += [pscustomobject]@{ Name = $k; Path = $dir }
  } else {
    $absent += $k
  }
}
if ($Only) {
  $bad = $Only | Where-Object { $TARGETS.Keys -notcontains $_ }
  if ($bad) {
    Write-Host ("[ERR] 未知目标: " + ($bad -join ', ')) -ForegroundColor Red
    Write-Host ("      可选: " + ($TARGETS.Keys -join ', ')) -ForegroundColor DarkGray
    exit 2
  }
}
if ($active.Count -eq 0) {
  Write-Host '[ERR] 没有找到任何已安装的 agent 目录' -ForegroundColor Red
  exit 2
}

function Read-FmName([string]$md) {
  $raw = Get-Content -LiteralPath $md -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return '' }
  $raw = $raw.TrimStart([char]0xFEFF)
  $m = [regex]::Match($raw, '(?s)\A---\r?\n(.*?)\r?\n---')
  if (-not $m.Success) { return '' }
  $nm = [regex]::Match($m.Groups[1].Value, '(?m)^name:\s*(.+?)\s*$')
  if ($nm.Success) { return $nm.Groups[1].Value.Trim('"', "'") }
  return ''
}

# ---- 2. 扫描实体目录里的 skill ----
$all = @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -notmatch '^[._]'
} | Sort-Object Name)

$skills = @()
$invalid = @()
foreach ($d in $all) {
  $md = Join-Path $d.FullName 'SKILL.md'
  if (-not (Test-Path -LiteralPath $md)) {
    if ((Get-ChildItem -LiteralPath $d.FullName -Recurse -File -ErrorAction SilentlyContinue).Count -gt 0) {
      $invalid += [pscustomobject]@{ Name = $d.Name; Why = '缺 SKILL.md' }
    }
    continue
  }
  $fmName = Read-FmName $md
  if ($fmName -ne $d.Name) {
    $why = if ([string]::IsNullOrWhiteSpace($fmName)) { 'frontmatter 缺 name 或格式不对' } else { "name 为 '$fmName'，与目录名不一致" }
    $invalid += [pscustomobject]@{ Name = $d.Name; Why = $why }
    continue
  }
  $skills += $d
}

# ---- 2b. 不合规的 skill 不挂，避免在每个 agent 里埋雷 ----
if ($invalid.Count -gt 0) {
  Write-Host '跳过（不合规，先修好再挂）' -ForegroundColor Yellow
  foreach ($i in $invalid) {
    Write-Host ("    {0,-24} {1}" -f $i.Name, $i.Why) -ForegroundColor Red
  }
  Write-Host '    详细诊断跑 check.cmd' -ForegroundColor DarkGray
  Write-Host ''
}

if ($skills.Count -eq 0) {
  Write-Host '没有可挂载的 skill。' -ForegroundColor Yellow
  Write-Host '放一个进来再跑一次，形如：skills\<名称>\SKILL.md（name 字段须与目录名一致）' -ForegroundColor DarkGray
  Write-Host ''
  if ($invalid.Count -gt 0) { exit 1 }
  exit 0
}

# ---- 3. 逐个挂 ----
$okCount = 0
$skipCount = 0
$failCount = 0

foreach ($s in $skills) {
  Write-Host ($s.Name) -ForegroundColor White
  foreach ($t in $active) {
    $link = Join-Path $t.Path $s.Name
    $want = Norm($s.FullName)

    if (Test-Path -LiteralPath $link) {
      $cur = Get-JunctionTarget $link
      if ($cur -and (Norm($cur) -eq $want)) {
        Write-Host ("    {0,-10} 已挂载，指向正确" -f $t.Name) -ForegroundColor DarkGray
        $skipCount++
        continue
      }
      if (-not $cur) {
        Write-Host ("    {0,-10} [冲突] 已存在同名真实目录，未覆盖" -f $t.Name) -ForegroundColor Red
        $failCount++
        continue
      }
      if (-not $Force) {
        Write-Host ("    {0,-10} [冲突] 已指向 {1}，加 -Force 才重建" -f $t.Name, $cur) -ForegroundColor Yellow
        $failCount++
        continue
      }
      if ($DryRun) {
        Write-Host ("    {0,-10} 将重建（原指向 {1}）" -f $t.Name, $cur) -ForegroundColor Yellow
        $okCount++
        continue
      }
      [System.IO.Directory]::Delete($link, $false)
    }

    if ($DryRun) {
      Write-Host ("    {0,-10} 将创建链接" -f $t.Name) -ForegroundColor Yellow
      $okCount++
      continue
    }
    try {
      if (-not (Test-Path -LiteralPath $t.Path)) {
        New-Item -ItemType Directory -Path $t.Path -Force | Out-Null
      }
      New-Item -ItemType Junction -Path $link -Target $s.FullName -ErrorAction Stop | Out-Null
      Write-Host ("    {0,-10} OK" -f $t.Name) -ForegroundColor Green
      $okCount++
    } catch {
      Write-Host ("    {0,-10} [失败] {1}" -f $t.Name, $_.Exception.Message) -ForegroundColor Red
      $failCount++
    }
  }
}

# ---- 4. 汇总 ----
Write-Host ''
Write-Host ("skill {0} 个 · 新建 {1} · 已存在 {2} · 异常 {3}" -f $skills.Count, $okCount, $skipCount, $failCount) -ForegroundColor Cyan
if ($absent.Count -gt 0) {
  Write-Host ("未安装，跳过: " + ($absent -join ', ')) -ForegroundColor DarkGray
}
if ($invalid.Count -gt 0) {
  Write-Host ("不合规已跳过 {0} 个: {1}" -f $invalid.Count, (($invalid | ForEach-Object { $_.Name }) -join ', ')) -ForegroundColor Red
}
Write-Host ''
Write-Host '改完要重开 agent 会话才生效（skills 在启动时扫描）。' -ForegroundColor DarkGray

if ($failCount -gt 0 -or $invalid.Count -gt 0) { exit 1 }
exit 0
