# BettaFish - one click local launcher (Windows / PowerShell)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run_bettafish.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run_bettafish.ps1 -Full -DbMode mysql
#
# Switches:
#   -Full            install full requirements.txt (PDF export, wordcloud, opencv, xgboost...)
#   -DbMode auto     (default) local MySQL on 3306 -> else local PostgreSQL 5432 -> else docker postgres:15
#   -DbMode mysql    use the local MySQL given by DB_* in .env (database auto-created if missing)
#   -DbMode postgres use PostgreSQL on 127.0.0.1:5432 (must already exist)
#   -DbMode docker   force a local postgres:15 container named bettafish-db (port 5432)
#   -DbMode none     do not touch the database at all
#   -SkipPlaywright  skip `playwright install chromium` (only needed by the crawler)
#   -SetupOnly       prepare venv + dependencies + .env, do not start the app
#   -Port <n>        rewrite PORT in .env (default 5000)
#   -PipIndex <url>  pip mirror (default tuna)
#
# NOTE: the parameter is -DbMode, not -Db: PowerShell reserves -db as an alias of the
# common switch -Debug, so a parameter literally named -Db cannot exist on an advanced
# function (it fails with "conflicts with the alias of parameter Debug").

[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$SkipPlaywright,
    [switch]$SetupOnly,
    [ValidateSet('auto', 'mysql', 'postgres', 'docker', 'none')]
    [Alias('Database', 'DbKind')]
    [string]$DbMode = 'auto',
    [int]$Port = 5000,
    [string]$PipIndex = 'https://pypi.tuna.tsinghua.edu.cn/simple'
)

# 外部命令（python/pip/docker）往 stderr 写东西在 PS 5.1 + Stop 下会抛 NativeCommandError，
# 这里保持 Continue，把中断交给显式的 exit 和 $LASTEXITCODE 判断。
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Say([string]$msg, [string]$color = 'Cyan') {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor $color
}
function Warn([string]$msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. python
Say "1/6 locate python"
$PyExe = $null
$PyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @('-3.11', '-3.10', '-3.12', '-3.13', '-3')) {
        & py $v -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $PyExe = 'py'; $PyArgs = @($v); break }
    }
}
if (-not $PyExe) {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { $PyExe = $p.Source; $PyArgs = @() }
}
if (-not $PyExe) {
    Write-Host "python not found. install Python 3.11 (https://www.python.org/downloads/) and retry." -ForegroundColor Red
    exit 1
}
$verRaw = ''
try { $verRaw = (& $PyExe @PyArgs -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null) } catch { $verRaw = '' }
$ver = ("$verRaw").Trim()
if (-not $ver) { $ver = 'unknown' }
Say "python $ver  ($PyExe $($PyArgs -join ' '))" 'Gray'
if ($ver -notmatch '^3\.(9|10|11|12|13)$') { Warn "python $ver is outside the tested range 3.9-3.13" }

# ---------------------------------------------------------------- 2. venv
Say "2/6 virtualenv"
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $PyExe @PyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "venv creation failed" -ForegroundColor Red; exit 1 }
}
function Py([string[]]$cli) {
    & $venvPy @cli
}
Say "using $venvPy" 'Gray'

# ---------------------------------------------------------------- 3. deps
Say "3/6 dependencies"
Py @('-m', 'pip', 'install', '--upgrade', 'pip', '-i', $PipIndex, '--quiet')
$reqFile = if ($Full) { 'requirements.txt' } else { 'requirements-core.txt' }
Say "installing from $reqFile  (mirror: $PipIndex)" 'Gray'
Py @('-m', 'pip', 'install', '-r', $reqFile, '-i', $PipIndex)
if ($LASTEXITCODE -ne 0) {
    Warn "bulk install failed, retrying package by package"
    $failed = @()
    $lines = Get-Content $reqFile | Where-Object { $_.Trim() -ne '' -and -not $_.Trim().StartsWith('#') }
    foreach ($line in $lines) {
        $spec = $line.Trim() -replace '\s+#.*$', ''
        if (-not $spec) { continue }
        Py @('-m', 'pip', 'install', $spec, '-i', $PipIndex, '--quiet')
        if ($LASTEXITCODE -ne 0) { $failed += $spec; Warn "skipped: $spec" }
    }
    if ($failed.Count -gt 0) { Warn ("not installed: " + ($failed -join ', ')) }
}

# ---------------------------------------------------------------- 4. playwright
if (-not $SkipPlaywright) {
    Say "4/6 playwright chromium"
    Py @('-m', 'playwright', 'install', 'chromium')
    if ($LASTEXITCODE -ne 0) { Warn "playwright install failed - crawler (MindSpider) will not work, UI is fine" }
}
else {
    Say "4/6 playwright chromium (skipped)" 'Gray'
}

# ---------------------------------------------------------------- 5. env + db
Say "5/6 .env and database"
$envPath = Join-Path $root '.env'
if (-not (Test-Path $envPath)) {
    Copy-Item (Join-Path $root '.env.example') $envPath
    Warn ".env created from .env.example - fill in the API keys before running an analysis"
}

function Set-EnvValue([string]$key, [string]$value) {
    # 逐行重写，避免正则替换时 value 里的 $ / \ 被当成替换模式
    $keyPattern = '^\s*' + [regex]::Escape($key) + '\s*='
    $lines = [System.IO.File]::ReadAllLines($envPath)
    $out = New-Object System.Collections.Generic.List[string]
    $found = $false
    foreach ($line in $lines) {
        if ($line -match $keyPattern) { $out.Add("$key=$value"); $found = $true }
        else { $out.Add($line) }
    }
    if (-not $found) { $out.Add("$key=$value") }
    [System.IO.File]::WriteAllLines($envPath, $out, (New-Object System.Text.UTF8Encoding($false)))
}

Set-EnvValue 'PORT' "$Port"

function Test-Port([string]$h, [int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $c.ConnectAsync($h, $p)
        if (-not $task.Wait([TimeSpan]::FromMilliseconds(1500))) { return $false }
        return $c.Connected
    }
    catch { return $false }
    finally { try { $c.Close() } catch { } }
}

function Get-EnvValue([string]$key, [string]$default) {
    $raw = [System.IO.File]::ReadAllText($envPath)
    $m = [regex]::Match($raw, '(?m)^' + [regex]::Escape($key) + '=(.*)$')
    if ($m.Success) {
        $v = $m.Groups[1].Value.Trim()
        if ($v) { return $v }
    }
    return $default
}

# ------------------------------------------------------------ database mode
$dbPort    = [int](Get-EnvValue 'DB_PORT' '3306')
$resolvedDb = $DbMode
if ($resolvedDb -eq 'auto') {
    if (Test-Port '127.0.0.1' $dbPort) { $resolvedDb = 'mysql' }
    elseif (Test-Port '127.0.0.1' 5432) { $resolvedDb = 'postgres' }
    elseif (Get-Command docker -ErrorAction SilentlyContinue) { $resolvedDb = 'docker' }
    else { $resolvedDb = 'none' }
    Say "database auto-detect -> $resolvedDb" 'Gray'
}

switch ($resolvedDb) {
    'mysql' {
        Say "database: local MySQL $((Get-EnvValue 'DB_HOST' '127.0.0.1')):$(Get-EnvValue 'DB_PORT' '3306') " 'Gray'
        Set-EnvValue 'DB_DIALECT' 'mysql'
        # 库不存在时自动创建（scripts/init_mysql_db.py，只 CREATE DATABASE IF NOT EXISTS）
        Py @('scripts\init_mysql_db.py')
        if ($LASTEXITCODE -ne 0) {
            Warn "could not reach MySQL or create the database - check DB_USER / DB_PASSWORD / DB_PORT in .env"
        }
    }
    'postgres' {
        Say "database: PostgreSQL 127.0.0.1:5432 (must already exist)" 'Gray'
        Set-EnvValue 'DB_DIALECT' 'postgresql'
        Set-EnvValue 'DB_PORT' '5432'
        Warn "credentials come from DB_USER / DB_PASSWORD in .env - make sure they are a valid PostgreSQL account, not the MySQL one"
    }
    'docker' {
        Say "database: docker postgres:15 container bettafish-db" 'Gray'
        Set-EnvValue 'DB_DIALECT' 'postgresql'
        Set-EnvValue 'DB_PORT' '5432'
        Set-EnvValue 'DB_HOST' '127.0.0.1'
        Set-EnvValue 'DB_USER' 'bettafish'
        Set-EnvValue 'DB_PASSWORD' 'bettafish'
        Set-EnvValue 'DB_NAME' 'bettafish'
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            $names = docker ps -a --filter "name=bettafish-db" --format "{{.Names}}"
            if ($names -match 'bettafish-db') {
                Say "starting existing container bettafish-db" 'Gray'
                docker start bettafish-db | Out-Null
            }
            else {
                Say "creating postgres:15 container bettafish-db on 127.0.0.1:5432" 'Gray'
                docker run -d --name bettafish-db `
                    -e POSTGRES_USER=bettafish -e POSTGRES_PASSWORD=bettafish -e POSTGRES_DB=bettafish `
                    -p 5432:5432 postgres:15 | Out-Null
            }
            if ($LASTEXITCODE -ne 0) {
                Warn "docker is not usable (is Docker Desktop running?) - skipping database startup"
            }
            else {
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 2
                    if (Test-Port '127.0.0.1' 5432) { Say "postgres is up on 127.0.0.1:5432" 'Gray'; break }
                    if ($i -eq 29) { Warn "postgres container did not open 5432 within 60s - check 'docker logs bettafish-db'" }
                }
            }
        }
        else {
            Warn "docker not found - install Docker Desktop or use -DbMode mysql / none"
        }
    }
    'none' {
        Say "database: skipped (-DbMode none)" 'Gray'
    }
}

if ($resolvedDb -ne 'none') {
    Say "initializing database schema (best effort)" 'Gray'
    Py @('MindSpider\main.py', '--init-db')
    if ($LASTEXITCODE -ne 0) { Warn "database init failed - UI still starts, Insight Agent has no data source" }
}

if ($SetupOnly) {
    Say "setup finished (-SetupOnly)" 'Green'
    Write-Host "start later with:  .\.venv\Scripts\python.exe app.py"
    exit 0
}

# ---------------------------------------------------------------- 6. run
Say "6/6 start Flask orchestrator" 'Green'
Write-Host "open  http://127.0.0.1:$Port  then press the start button in the page." -ForegroundColor Green
Write-Host "streamlit agents will bind 8501/8502/8503 after that click." -ForegroundColor DarkGray
Py @('app.py')
