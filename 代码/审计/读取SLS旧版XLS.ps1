<#
.SYNOPSIS
只读解析 Mendeley SLS TPU 来源中的 59 个旧版 XLS 工作簿。

.DESCRIPTION
脚本从自身位置推导项目根目录，只允许读取固定的
01_原始数据/外部数据/新增开放数据/Mendeley_SLS_TPU工艺力学/结构化表格
目录。Excel 工作簿始终以 ReadOnly 模式打开，不转换也不保存；脚本
只把工作簿、工作表、有限数值单元格和试样曲线复算结果以压缩 JSON
输出到标准输出，供同目录的《新增开放数据工作簿双源.py》消费。

.PARAMETER 目录
可省略。若提供，解析后的绝对路径必须与上述固定原始数据目录完全一致。
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
$results = [System.Collections.Generic.List[object]]::new()

function Assert-NotReparsePoint([System.IO.FileSystemInfo]$item) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "拒绝读取符号链接、联接点或其他重解析点：$($item.FullName)"
    }
}

function Matrix-Value($matrix, [int]$row, [int]$column, [int]$rows, [int]$columns) {
    if ($rows -eq 1 -and $columns -eq 1) {
        return $matrix
    }
    return $matrix.GetValue($row, $column)
}

function Finite-Number-Or-Null($value) {
    if ($null -eq $value -or $value -is [bool] -or $value -is [string]) {
        return $null
    }
    if (
        $value -is [byte] -or $value -is [sbyte] -or
        $value -is [int16] -or $value -is [uint16] -or
        $value -is [int32] -or $value -is [uint32] -or
        $value -is [int64] -or $value -is [uint64] -or
        $value -is [single] -or $value -is [double] -or
        $value -is [decimal]
    ) {
        $number = [Convert]::ToDouble($value, $culture)
        if (-not [double]::IsNaN($number) -and -not [double]::IsInfinity($number)) {
            return $number
        }
    }
    return $null
}

function Sha256-Text([string]$text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
        $hash = $sha.ComputeHash($bytes)
        return [BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

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

$expectedDirectory = Join-Path $projectRoot '01_原始数据\外部数据\新增开放数据\Mendeley_SLS_TPU工艺力学\结构化表格'
if (-not (Test-Path -LiteralPath $expectedDirectory -PathType Container)) {
    throw "缺少固定的 SLS 结构化表格目录：$expectedDirectory"
}
if ([string]::IsNullOrWhiteSpace($目录)) {
    $目录 = $expectedDirectory
}
$resolvedExpected = (Resolve-Path -LiteralPath $expectedDirectory).Path
$resolvedInput = (Resolve-Path -LiteralPath $目录).Path
if (-not [string]::Equals($resolvedInput, $resolvedExpected, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝读取固定原始数据目录之外的路径：$resolvedInput"
}
Assert-NotReparsePoint (Get-Item -LiteralPath $resolvedInput -Force)

$inputFiles = @(Get-ChildItem -LiteralPath $resolvedInput -Filter '*.xls' -File | Sort-Object Name)
if ($inputFiles.Count -ne 59) {
    throw "SLS 旧版 XLS 文件数不符；期望 59，实际 $($inputFiles.Count)"
}
foreach ($file in $inputFiles) {
    Assert-NotReparsePoint $file
}

try {
    try {
        $excel = New-Object -ComObject Excel.Application
    } catch {
        throw "无法启动 Excel COM；59 个旧版 XLS 的只读审计需要本机 Microsoft Excel。原始错误：$($_.Exception.Message)"
    }
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
    $excel.AskToUpdateLinks = $false

    foreach ($file in $inputFiles) {
        $workbook = $null
        $workbookCurves = [System.Collections.Generic.List[object]]::new()
        $moduleValues = [System.Collections.Generic.List[double]]::new()
        $sheetCount = 0
        $nonemptyCells = 0L
        $finiteNumericCells = 0L
        $formulaCells = 0L
        try {
            # Workbooks.Open 的第三个位置参数是 ReadOnly=$true。
            $workbook = $excel.Workbooks.Open($file.FullName, 0, $true)
            if (-not $workbook.ReadOnly) {
                throw "工作簿未以 ReadOnly 模式打开，停止审计：$($file.FullName)"
            }

            $worksheetCount = $workbook.Worksheets.Count
            for ($sheetIndex = 1; $sheetIndex -le $worksheetCount; $sheetIndex++) {
                $sheet = $null
                $usedRange = $null
                try {
                    $sheet = $workbook.Worksheets.Item($sheetIndex)
                    $usedRange = $sheet.UsedRange
                    $rows = [int]$usedRange.Rows.Count
                    $columns = [int]$usedRange.Columns.Count
                    $values = $usedRange.Value2
                    $formulas = $usedRange.Formula
                    $sheetCount++

                    for ($row = 1; $row -le $rows; $row++) {
                        for ($column = 1; $column -le $columns; $column++) {
                            $value = Matrix-Value $values $row $column $rows $columns
                            $isEmptyString = $value -is [string] -and $value.Length -eq 0
                            if ($null -ne $value -and -not $isEmptyString) {
                                $nonemptyCells++
                                if ($null -ne (Finite-Number-Or-Null $value)) {
                                    $finiteNumericCells++
                                }
                            }
                            $formula = Matrix-Value $formulas $row $column $rows $columns
                            if ($formula -is [string] -and $formula.StartsWith('=')) {
                                $formulaCells++
                            }
                        }
                    }

                    if ($sheet.Name -match '^\s*Éprouvette\s+\d+\s*$') {
                        if ($usedRange.Row -ne 1 -or $columns -lt 2) {
                            throw "未识别的试样工作表布局：$($file.Name)/$($sheet.Name)"
                        }
                        $xHeader = [string](Matrix-Value $values 2 1 $rows $columns)
                        $yHeader = [string](Matrix-Value $values 2 2 $rows $columns)
                        if ($xHeader -ne 'Allongement' -or $yHeader -ne 'Force standard') {
                            throw "试样工作表缺少 Allongement/Force standard 主轴：$($file.Name)/$($sheet.Name)"
                        }
                        $builder = [System.Text.StringBuilder]::new()
                        $pointCount = 0L
                        for ($row = 4; $row -le $rows; $row++) {
                            $x = Finite-Number-Or-Null (Matrix-Value $values $row 1 $rows $columns)
                            $y = Finite-Number-Or-Null (Matrix-Value $values $row 2 $rows $columns)
                            if ($null -ne $x -and $null -ne $y) {
                                $pointCount++
                                [void]$builder.Append($x.ToString('R', $culture))
                                [void]$builder.Append(',')
                                [void]$builder.Append($y.ToString('R', $culture))
                                [void]$builder.Append("`n")
                            }
                        }
                        $workbookCurves.Add([pscustomobject]@{
                            sheet = [string]$sheet.Name
                            point_count = $pointCount
                            curve_sha256 = Sha256-Text $builder.ToString()
                        })
                    }

                    if ($sheet.Name -like 'Résultats*' -and $columns -ge 6) {
                        for ($row = 3; $row -le $rows; $row++) {
                            $module = Finite-Number-Or-Null (Matrix-Value $values $row 6 $rows $columns)
                            if ($null -ne $module) {
                                $moduleValues.Add($module)
                            }
                        }
                    }
                } finally {
                    if ($null -ne $usedRange) {
                        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($usedRange)
                    }
                    if ($null -ne $sheet) {
                        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sheet)
                    }
                }
            }

            if ($workbookCurves.Count -notin 4, 5) {
                throw "XLS 试样曲线数不是 4 或 5：$($file.Name) = $($workbookCurves.Count)"
            }
            $sequenceBuilder = [System.Text.StringBuilder]::new()
            foreach ($curve in $workbookCurves) {
                [void]$sequenceBuilder.Append($curve.curve_sha256)
                [void]$sequenceBuilder.Append("`n")
            }
            $negativeModules = @($moduleValues | Where-Object { $_ -lt 0 })
            $results.Add([pscustomobject]@{
                file = $file.Name
                read_only = [bool]$workbook.ReadOnly
                sheet_count = $sheetCount
                nonempty_cells = $nonemptyCells
                finite_numeric_cells = $finiteNumericCells
                formula_cells = $formulaCells
                specimen_count = $workbookCurves.Count
                curve_point_count = [long](($workbookCurves | Measure-Object -Property point_count -Sum).Sum)
                sequence_sha256 = Sha256-Text $sequenceBuilder.ToString()
                curves = @($workbookCurves)
                module_values_mpa = @($moduleValues)
                negative_module_values_mpa = $negativeModules
            })
        } finally {
            if ($null -ne $workbook) {
                $workbook.Close($false)
                [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
            }
        }
    }
} finally {
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

$results | ConvertTo-Json -Depth 8 -Compress
