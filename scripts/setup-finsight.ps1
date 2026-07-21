[CmdletBinding()]
param(
    [string]$FinSightRoot = "",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "FinSight 深度研报仅支持 Windows。"
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $FinSightRoot) {
    $FinSightRoot = Join-Path $ProjectRoot "vendor\finsight"
}
if (-not (Test-Path -LiteralPath $FinSightRoot)) {
    throw "未找到 FinSight runtime：$FinSightRoot。请先执行 git submodule update --init --recursive。"
}
$ResolvedFinSightRoot = (Resolve-Path -LiteralPath $FinSightRoot).Path
$LockPath = Join-Path $ResolvedFinSightRoot "requirements-tickflow-lock.txt"
$VenvPython = Join-Path $ResolvedFinSightRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $PyLauncher) {
        throw "未找到 Python Launcher。请先安装 Python 3.10.20 x64。"
    }
    & $PyLauncher.Source -3.10 -m venv (Join-Path $ResolvedFinSightRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "创建 FinSight Python 3.10 虚拟环境失败。"
    }
}

if (-not $SkipDependencyInstall) {
    if (-not (Test-Path -LiteralPath $LockPath)) {
        throw "缺少锁定依赖文件：$LockPath"
    }
    & $VenvPython -m pip install --disable-pip-version-check -r $LockPath
    if ($LASTEXITCODE -ne 0) {
        throw "安装 FinSight 锁定依赖失败。"
    }
}

$FinSightEnv = Join-Path $ResolvedFinSightRoot ".env"
$FinSightEnvExample = Join-Path $ResolvedFinSightRoot ".env.example"
if (-not (Test-Path -LiteralPath $FinSightEnv) -and (Test-Path -LiteralPath $FinSightEnvExample)) {
    Copy-Item -LiteralPath $FinSightEnvExample -Destination $FinSightEnv
    Write-Host "已创建 $FinSightEnv；请填写各模型与搜索 API Key。" -ForegroundColor Yellow
}

& (Join-Path $PSScriptRoot "verify-finsight.ps1") -FinSightRoot $ResolvedFinSightRoot
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "FinSight runtime 初始化完成。" -ForegroundColor Green
