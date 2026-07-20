[CmdletBinding()]
param(
    [string]$BaseUrl = "http://localhost:3018",
    [string]$Symbol = "000001.SZ",
    [string]$Name = "平安银行",
    [int]$TimeoutMinutes = 90,
    [switch]$ConfirmModelCost
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmModelCost) {
    throw "该检查会真实调用模型并产生费用。确认后请增加 -ConfirmModelCost。"
}

$ApiRoot = $BaseUrl.TrimEnd("/") + "/api/stock-analysis/deep-reports"
$Health = Invoke-RestMethod -Uri "$ApiRoot/health" -Method Get
if (-not $Health.ready) {
    throw "FinSight 环境未就绪：$($Health.warnings -join '; ')"
}

$Catalog = Invoke-RestMethod -Uri "$ApiRoot/task-catalog" -Method Get
$CollectIds = @($Catalog.collect_tasks | ForEach-Object { $_.id })
$AnalysisIds = @($Catalog.analysis_tasks | ForEach-Object { $_.id })
if ($CollectIds.Count -eq 0 -or $AnalysisIds.Count -eq 0) {
    throw "研报任务目录为空。"
}

$Body = @{
    symbol = $Symbol
    name = $Name
    collect_task_ids = $CollectIds
    analysis_task_ids = $AnalysisIds
} | ConvertTo-Json -Depth 5

$Created = Invoke-RestMethod -Uri "$ApiRoot/runs" -Method Post -ContentType "application/json" -Body $Body
$RunId = $Created.run.id
Write-Host "已创建研报任务：$RunId"

$Deadline = (Get-Date).AddMinutes($TimeoutMinutes)
do {
    Start-Sleep -Seconds 10
    $Detail = Invoke-RestMethod -Uri "$ApiRoot/runs/$RunId" -Method Get
    $Run = $Detail.run
    Write-Host ("{0,3}% {1} - {2}" -f $Run.progress, $Run.stage, $Run.message)
    if ($Run.status -in @("succeeded", "failed", "cancelled")) {
        break
    }
} while ((Get-Date) -lt $Deadline)

if ($Run.status -ne "succeeded") {
    throw "研报未成功完成，状态=$($Run.status)，错误=$($Run.error)"
}

$OutputRoot = Join-Path $env:TEMP "TickFlowDeepReportSmoke\$RunId"
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
foreach ($Kind in @("markdown", "word", "pdf", "log")) {
    $Artifact = $Run.artifacts.$Kind
    if ($Artifact.available) {
        $Destination = Join-Path $OutputRoot $Artifact.filename
        Invoke-WebRequest -Uri ($BaseUrl.TrimEnd("/") + $Artifact.download_url) -OutFile $Destination
        Write-Host "已下载 $Kind：$Destination"
    }
}

Write-Host "深度研报冒烟检查通过：$OutputRoot" -ForegroundColor Green
