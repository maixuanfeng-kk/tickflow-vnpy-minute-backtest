[CmdletBinding()]
param(
    [string]$FinSightRoot = ""
)

$ErrorActionPreference = "Stop"
$Failures = [System.Collections.Generic.List[string]]::new()

function Add-Failure([string]$Message) {
    $Failures.Add($Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Add-Success([string]$Message) {
    Write-Host "[ OK ] $Message" -ForegroundColor Green
}

if ($env:OS -ne "Windows_NT") {
    Add-Failure "当前系统不是 Windows。"
} elseif (-not [Environment]::Is64BitOperatingSystem) {
    Add-Failure "深度研报要求 Windows x64。"
} else {
    Add-Success "Windows x64"
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $FinSightRoot) {
    $FinSightRoot = Join-Path $ProjectRoot "vendor\finsight"
}
if (-not (Test-Path -LiteralPath $FinSightRoot)) {
    Add-Failure "未找到 FinSight runtime：$FinSightRoot"
    exit 1
}
$ResolvedFinSightRoot = (Resolve-Path -LiteralPath $FinSightRoot).Path
$ManifestPath = Join-Path $ResolvedFinSightRoot "finsight-windows-lock.json"
if (-not (Test-Path -LiteralPath $ManifestPath)) {
    Add-Failure "缺少环境清单：$ManifestPath"
    exit 1
}
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$GitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $GitCommand) {
    Add-Failure "未找到 Git，无法校验 FinSight submodule 提交。"
} else {
    $FinSightCommit = (& $GitCommand.Source -C $ResolvedFinSightRoot rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $FinSightCommit) {
        Add-Failure "无法读取 FinSight Git 提交。"
    } else {
        & $GitCommand.Source -C $ResolvedFinSightRoot merge-base --is-ancestor $Manifest.base_commit $FinSightCommit
        if ($LASTEXITCODE -ne 0) {
            Add-Failure "FinSight 当前提交不是锁定官方基线 $($Manifest.base_commit) 的后代。"
        } else {
            Add-Success "FinSight 官方基线 $($Manifest.base_commit.Substring(0, 12))"
        }
        $TrackedChanges = @(& $GitCommand.Source -C $ResolvedFinSightRoot status --porcelain --untracked-files=no)
        if ($TrackedChanges.Count -gt 0) {
            Add-Failure "FinSight submodule 含未提交的受跟踪文件变更。"
        }
    }

    $ProjectUri = [Uri]($ProjectRoot.TrimEnd("\") + "\")
    $FinSightUri = [Uri]$ResolvedFinSightRoot
    $RelativeFinSight = [Uri]::UnescapeDataString(
        $ProjectUri.MakeRelativeUri($FinSightUri).ToString()
    )
    $SubmoduleState = @(& $GitCommand.Source -C $ProjectRoot submodule status -- $RelativeFinSight)
    if ($LASTEXITCODE -eq 0 -and $SubmoduleState.Count -eq 1) {
        if ($SubmoduleState[0].StartsWith(" ")) {
            Add-Success "FinSight checkout 与主仓库 submodule 指针一致"
        } else {
            Add-Failure "FinSight checkout 未与主仓库 submodule 指针对齐。"
        }
    } else {
        Add-Failure "无法从主仓库读取 FinSight submodule 状态。"
    }
}

$VenvPython = Join-Path $ResolvedFinSightRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Add-Failure "缺少 FinSight Python：$VenvPython"
} else {
    $PythonVersion = (& $VenvPython -c "import platform; print(platform.python_version())").Trim()
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "无法执行 FinSight Python。"
    } elseif ($PythonVersion -ne $Manifest.runtime.python) {
        Add-Failure "Python 版本为 $PythonVersion，要求 $($Manifest.runtime.python)。"
    } else {
        Add-Success "Python $PythonVersion"
    }

    $PackageVersions = & $VenvPython -c "import importlib.metadata as m; print(m.version('python-docx')); print(m.version('docx2pdf'))"
    if ($LASTEXITCODE -ne 0 -or @($PackageVersions).Count -ne 2) {
        Add-Failure "无法读取 python-docx/docx2pdf 版本。"
        $PackageVersions = @("", "")
    }
    if ($PackageVersions[0].Trim() -ne $Manifest.runtime.python_docx) {
        Add-Failure "python-docx 版本为 $($PackageVersions[0])，要求 $($Manifest.runtime.python_docx)。"
    } else {
        Add-Success "python-docx $($PackageVersions[0])"
    }
    if ($PackageVersions[1].Trim() -ne $Manifest.runtime.docx2pdf) {
        Add-Failure "docx2pdf 版本为 $($PackageVersions[1])，要求 $($Manifest.runtime.docx2pdf)。"
    } else {
        Add-Success "docx2pdf $($PackageVersions[1])"
    }
}

$PandocCandidates = @(
    (Join-Path $ResolvedFinSightRoot ".venv\Library\bin\pandoc.exe"),
    (Join-Path $env:LOCALAPPDATA "Pandoc\pandoc.exe")
)
if ($env:PANDOC_PATH) {
    $PandocCandidates = @($env:PANDOC_PATH) + $PandocCandidates
}
$PandocCommand = Get-Command pandoc.exe -ErrorAction SilentlyContinue
if ($PandocCommand) {
    $PandocCandidates += $PandocCommand.Source
}
$PandocPath = $PandocCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $PandocPath) {
    Add-Failure "未找到 Pandoc $($Manifest.runtime.pandoc)。"
} else {
    $PandocFirstLine = (& $PandocPath --version | Select-Object -First 1).Trim()
    if ($PandocFirstLine -ne "pandoc $($Manifest.runtime.pandoc)") {
        Add-Failure "Pandoc 为 '$PandocFirstLine'，要求 pandoc $($Manifest.runtime.pandoc)。"
    } else {
        Add-Success $PandocFirstLine
    }
}

$WordCandidates = @(
    "C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "C:\Program Files (x86)\Microsoft Office\root\Office16\WINWORD.EXE"
)
$WordRegistryKeys = @(
    "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe",
    "Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe",
    "Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"
)
foreach ($RegistryKey in $WordRegistryKeys) {
    try {
        $RegistryItem = Get-Item -LiteralPath $RegistryKey -ErrorAction Stop
        $RegistryWordPath = $RegistryItem.GetValue("")
        if ($RegistryWordPath) {
            $WordCandidates += [string]$RegistryWordPath
        }
    } catch {
        # 该注册表位置不存在时继续检查其他安装位置。
    }
}
$WordPath = $WordCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $WordPath) {
    Add-Failure "未找到 Microsoft Word。"
} else {
    $WordVersion = (Get-Item -LiteralPath $WordPath).VersionInfo.ProductVersion
    if ($WordVersion -ne $Manifest.runtime.word) {
        Add-Failure "Word 版本为 $WordVersion，参考版本为 $($Manifest.runtime.word)。"
    } else {
        Add-Success "Microsoft Word $WordVersion"
    }
}

foreach ($ArtifactProperty in $Manifest.artifacts.PSObject.Properties) {
    $ArtifactPath = Join-Path $ResolvedFinSightRoot $ArtifactProperty.Name
    if (-not (Test-Path -LiteralPath $ArtifactPath)) {
        Add-Failure "缺少版式资源：$($ArtifactProperty.Name)"
        continue
    }
    $ActualHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash
    if ($ActualHash -ne $ArtifactProperty.Value) {
        Add-Failure "$($ArtifactProperty.Name) 哈希不匹配。"
    } else {
        Add-Success "$($ArtifactProperty.Name) 哈希已锁定"
    }
}

try {
    Add-Type -AssemblyName System.Drawing
    $InstalledFonts = (New-Object System.Drawing.Text.InstalledFontCollection).Families.Name
    foreach ($FontName in $Manifest.fonts) {
        if ($InstalledFonts -notcontains $FontName) {
            Add-Failure "缺少字体：$FontName"
        } else {
            Add-Success "字体：$FontName"
        }
    }
} catch {
    Add-Failure "无法读取 Windows 字体列表：$($_.Exception.Message)"
}

$FinSightEnv = Join-Path $ResolvedFinSightRoot ".env"
if (-not (Test-Path -LiteralPath $FinSightEnv)) {
    Add-Failure "缺少 FinSight .env。"
} else {
    $RequiredKeys = @(
        "DS_MODEL_NAME", "DS_BASE_URL", "DS_API_KEY",
        "EMBEDDING_MODEL_NAME", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY",
        "VLM_MODEL_NAME", "VLM_BASE_URL", "VLM_API_KEY", "SERPER_API_KEY"
    )
    $EnvLines = Get-Content -LiteralPath $FinSightEnv -Encoding UTF8
    foreach ($Key in $RequiredKeys) {
        $Configured = $EnvLines | Where-Object { $_ -match ("^" + [regex]::Escape($Key) + "=.+$") }
        if (-not $Configured) {
            Add-Failure "FinSight .env 未配置 $Key。"
        }
    }
    if ($RequiredKeys.Count -eq ($RequiredKeys | Where-Object {
        $Key = $_
        $EnvLines | Where-Object { $_ -match ("^" + [regex]::Escape($Key) + "=.+$") }
    }).Count) {
        Add-Success "FinSight 密钥项已配置（值未显示）"
    }
}

if ($Failures.Count -gt 0) {
    Write-Host "FinSight 检查失败：$($Failures.Count) 项。" -ForegroundColor Red
    exit 1
}

Write-Host "FinSight Windows 环境检查通过。" -ForegroundColor Green
exit 0
