<#
.SYNOPSIS
只读解析 DRUM 低天花板来源中的四个旧版 XLS DMTA 工作簿。

.DESCRIPTION
脚本从自身位置推导项目根目录，并只允许访问固定的
数据/原始/外部数据/新增开放数据/DRUM_TPUU_低天花板/解包内容/Raw_Mechanical_Testing
目录。Excel 工作簿始终以 ReadOnly 模式打开，不保存、不转换、不写入文件；结果仅以
压缩 JSON 输出到标准输出，供同目录 DRUM_TPUU.py 消费。

.PARAMETER 目录
可省略。若提供，解析后的绝对路径必须与上述固定原始数据目录完全一致。

.EXAMPLE
pwsh -NoProfile -File .\读取低天花板DMTA.ps1
#>
[CmdletBinding()]
param(
    [string]$目录
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$excel = $null
$results = @()

$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    throw '无法确定脚本位置；请通过 -File 或脚本路径执行。'
}
$scriptDirectory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($scriptPath))
$projectRoot = [IO.Path]::GetFullPath((Join-Path $scriptDirectory '..\..'))
$projectMarker = Join-Path $projectRoot 'pyproject.toml'
if (-not (Test-Path -LiteralPath $projectMarker -PathType Leaf)) {
    throw "无法确认项目根目录（缺少 pyproject.toml）：$projectRoot"
}
$expectedDirectory = Join-Path $projectRoot '数据/原始\外部数据\新增开放数据\DRUM_TPUU_低天花板\解包内容\Raw_Mechanical_Testing'
if (-not (Test-Path -LiteralPath $expectedDirectory -PathType Container)) {
    throw "缺少固定的低天花板机械原始数据目录：$expectedDirectory"
}
if ([string]::IsNullOrWhiteSpace($目录)) {
    $目录 = $expectedDirectory
}
$resolvedExpected = (Resolve-Path -LiteralPath $expectedDirectory).Path
$resolvedInput = (Resolve-Path -LiteralPath $目录).Path
if (-not [string]::Equals($resolvedInput, $resolvedExpected, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝读取固定原始数据目录之外的路径：$resolvedInput"
}

$expectedFiles = @(
    'TPUU-C_DMTA.xls',
    'TPUU-D_DMTA.xls',
    'TPUU-R_DMTA.xls',
    'TPUU-S_DMTA.xls'
)
$inputFiles = @(Get-ChildItem -LiteralPath $resolvedInput -Filter '*_DMTA.xls' -File | Sort-Object Name)
$actualNames = @($inputFiles.Name)
$missingNames = @($expectedFiles | Where-Object { $_ -notin $actualNames })
$extraNames = @($actualNames | Where-Object { $_ -notin $expectedFiles })
if ($missingNames.Count -gt 0 -or $extraNames.Count -gt 0) {
    throw "DMTA XLS 文件集合不符；缺失=[$($missingNames -join ', ')]；额外=[$($extraNames -join ', ')]"
}

function Number-Or-Null($value) {
    if ($null -eq $value -or $value -eq '') { return $null }
    $parsed = 0.0
    if ([double]::TryParse([string]$value, [System.Globalization.NumberStyles]::Float, $culture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

try {
    try {
        $excel = New-Object -ComObject Excel.Application
    } catch {
        throw "无法启动 Excel COM；旧版 XLS 只读审计需要本机 Microsoft Excel。原始错误：$($_.Exception.Message)"
    }
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
    $inputFiles | ForEach-Object {
        $file = $_
        $details = $null
        $sheet = $null
        $workbook = $excel.Workbooks.Open($file.FullName, 0, $true)
        try {
            if (-not $workbook.ReadOnly) {
                throw "工作簿未以只读方式打开，停止审计：$($file.FullName)"
            }
            $details = $workbook.Worksheets.Item('Details')
            $sheet = $workbook.Worksheets.Item('Temperature Ramp - 1')
            $rows = $sheet.UsedRange.Rows.Count
            $pointCount = 0
            $dataRows = 0
            $missingPrimary = 0
            $secondaryMissing = 0
            $numericValues = 0
            $minTemp = $null
            $maxTemp = $null
            $minStorage = $null
            $maxStorage = $null
            $builder = [System.Text.StringBuilder]::new()
            for ($row = 4; $row -le $rows; $row++) {
                $values = @(
                    (Number-Or-Null $sheet.Cells.Item($row, 1).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 2).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 3).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 4).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 5).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 6).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 7).Value2),
                    (Number-Or-Null $sheet.Cells.Item($row, 8).Value2)
                )
                $nonNull = @($values | Where-Object { $null -ne $_ }).Count
                if ($nonNull -eq 0) { continue }
                $dataRows++
                $numericValues += $nonNull
                $temperature = $values[2]
                $tanDelta = $values[5]
                $storage = $values[6]
                $loss = $values[7]
                if ($null -ne $temperature -and $null -ne $storage) {
                    $pointCount++
                    if ($null -eq $minTemp -or $temperature -lt $minTemp) { $minTemp = $temperature }
                    if ($null -eq $maxTemp -or $temperature -gt $maxTemp) { $maxTemp = $temperature }
                    if ($null -eq $minStorage -or $storage -lt $minStorage) { $minStorage = $storage }
                    if ($null -eq $maxStorage -or $storage -gt $maxStorage) { $maxStorage = $storage }
                    [void]$builder.Append($temperature.ToString('R', $culture))
                    [void]$builder.Append(',')
                    [void]$builder.Append($storage.ToString('R', $culture))
                    [void]$builder.Append("`n")
                } else {
                    $missingPrimary++
                }
                if ($null -eq $tanDelta) { $secondaryMissing++ }
                if ($null -eq $loss) { $secondaryMissing++ }
            }
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $hashBytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($builder.ToString()))
                $curveHash = [Convert]::ToHexString($hashBytes).ToLowerInvariant()
            } finally {
                $sha.Dispose()
            }
            $results += [pscustomobject]@{
                file = $file.Name
                sheet = $sheet.Name
                test_name = [string]$details.Cells.Item(1, 2).Value2
                used_rows = $rows
                used_columns = $sheet.UsedRange.Columns.Count
                point_count = $pointCount
                data_rows = $dataRows
                missing_primary_rows = $missingPrimary
                secondary_missing_cells = $secondaryMissing
                numeric_value_count = $numericValues
                min_temperature_c = $minTemp
                max_temperature_c = $maxTemp
                min_storage_pa = $minStorage
                max_storage_pa = $maxStorage
                curve_sha256 = $curveHash
            }
        } finally {
            if ($null -ne $sheet) {
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sheet)
                $sheet = $null
            }
            if ($null -ne $details) {
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($details)
                $details = $null
            }
            $workbook.Close($false)
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
            $workbook = $null
        }
    }
} finally {
    if ($excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$results | ConvertTo-Json -Depth 6 -Compress
