<#
.SYNOPSIS
只读复算 Zenodo 标准化弹性体来源中的 Filaflex 60A 旧版 XLS 黏度数据。

.DESCRIPTION
脚本只允许读取固定的 Melting.zip 与固定成员
Melting/viscosity/Filaflex 60A.xls。成员仅解压到系统临时目录，核验
SHA-256 后由 Microsoft Excel COM 以 ReadOnly 模式打开；不保存、不转换，
最终只向标准输出返回压缩 JSON。临时目录在 finally 中经边界复核后删除。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$excel = $null
$workbook = $null
$zip = $null
$tempDirectory = $null

function Assert-NotReparsePoint([System.IO.FileSystemInfo]$item) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "拒绝读取重解析点：$($item.FullName)"
    }
}

function Matrix-Value($matrix, [int]$row, [int]$column, [int]$rows, [int]$columns) {
    if ($rows -eq 1 -and $columns -eq 1) {
        return $matrix
    }
    return $matrix.GetValue($row, $column)
}

function Finite-Number-Or-Throw($value, [string]$context) {
    if ($null -eq $value -or $value -is [bool]) {
        throw "缺失或布尔数值：$context"
    }
    try {
        $number = [Convert]::ToDouble($value, $culture)
    } catch {
        throw "非数值单元格：$context"
    }
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        throw "非有限数值：$context"
    }
    return $number
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
    throw '无法确定脚本位置；请通过 -File 调用。'
}
$scriptDirectory = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($scriptPath))
$projectRoot = [IO.Path]::GetFullPath((Join-Path $scriptDirectory '..\..'))
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -PathType Leaf)) {
    throw "无法确认项目根目录：$projectRoot"
}

$sourceDirectory = Join-Path $projectRoot '数据/原始\外部数据\新增开放数据\Zenodo_标准化弹性体表征'
$archivePath = Join-Path $sourceDirectory 'Melting.zip'
$memberName = 'Melting/viscosity/Filaflex 60A.xls'
$expectedArchiveSha256 = '9d902b31027a36f9e6a38e5fa5873dce289d0d0cad0088ab49458aa8b21adda4'
$expectedMemberSha256 = '3fc855fb76b452a1768df8b18e9edc270843bb9b370fd59213f6b9e2e3dc0295'

if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) {
    throw "缺少固定来源目录：$sourceDirectory"
}
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "缺少固定归档：$archivePath"
}
Assert-NotReparsePoint (Get-Item -LiteralPath $sourceDirectory -Force)
Assert-NotReparsePoint (Get-Item -LiteralPath $archivePath -Force)
$archiveSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveSha256 -ne $expectedArchiveSha256) {
    throw "Melting.zip SHA-256 漂移：$archiveSha256"
}

$expectedSheets = [ordered]@{
    'Temperature ramp - 1 - 0,312599 Hz' = @(96, 9, 'Temperature ramp 1')
    'Temperature ramp - 1 - 0,562301 Hz' = @(96, 9, 'Temperature ramp 1')
    'Temperature ramp - 1 - 1,0 Hz'      = @(95, 9, 'Temperature ramp 1')
    'Temperature ramp - 1 - 1,778 Hz'    = @(95, 9, 'Temperature ramp 1')
    'Temperature ramp - 1 - 3,12599 Hz'  = @(95, 9, 'Temperature ramp 1')
    'Flow ramp - 6'                       = @(25, 6, 'Flow ramp 6')
    'Temperature ramp - 3 - 0,312599 Hz' = @(242, 9, 'Temperature ramp 3')
    'Temperature ramp - 3 - 0,562301 Hz' = @(242, 9, 'Temperature ramp 3')
    'Temperature ramp - 3 - 1,0 Hz'      = @(242, 9, 'Temperature ramp 3')
    'Temperature ramp - 3 - 1,778 Hz'    = @(242, 9, 'Temperature ramp 3')
    'Temperature ramp - 3 - 3,12599 Hz'  = @(241, 9, 'Temperature ramp 3')
    'Temperature ramp - 4 - 0,312599 Hz' = @(87, 9, 'Temperature ramp 4')
    'Temperature ramp - 4 - 0,562301 Hz' = @(86, 9, 'Temperature ramp 4')
    'Temperature ramp - 4 - 1,0 Hz'      = @(86, 9, 'Temperature ramp 4')
    'Temperature ramp - 4 - 1,778 Hz'    = @(86, 9, 'Temperature ramp 4')
    'Temperature ramp - 4 - 3,12599 Hz'  = @(86, 10, 'Temperature ramp 4')
}

$curves = [System.Collections.Generic.List[object]]::new()
$groupCurveCounts = @{}
$groupPointCounts = @{}
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempDirectory = [IO.Path]::GetFullPath(
    (Join-Path $tempBase ("tpu-standard-xls-audit-" + [Guid]::NewGuid().ToString('N')))
)
$tempPrefix = $tempBase.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $tempDirectory.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "临时目录越界：$tempDirectory"
}
[void][IO.Directory]::CreateDirectory($tempDirectory)
$xlsPath = Join-Path $tempDirectory 'Filaflex 60A.xls'

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    $matches = @($zip.Entries | Where-Object { $_.FullName -ceq $memberName })
    if ($matches.Count -ne 1) {
        throw "固定 XLS 成员数不为 1：$($matches.Count)"
    }
    $entry = $matches[0]
    if ($entry.Length -ne 394752) {
        throw "固定 XLS 成员解压字节漂移：$($entry.Length)"
    }
    $inputStream = $null
    $outputStream = $null
    try {
        $inputStream = $entry.Open()
        $outputStream = [IO.File]::Open($xlsPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $inputStream.CopyTo($outputStream)
        $outputStream.Flush($true)
    } finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        if ($null -ne $inputStream) { $inputStream.Dispose() }
    }
    $zip.Dispose()
    $zip = $null

    $memberSha256 = (Get-FileHash -LiteralPath $xlsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($memberSha256 -ne $expectedMemberSha256) {
        throw "Filaflex 60A.xls SHA-256 漂移：$memberSha256"
    }

    try {
        $excel = New-Object -ComObject Excel.Application
    } catch {
        throw "无法启动 Excel COM；旧版 XLS 审计需要本机 Microsoft Excel：$($_.Exception.Message)"
    }
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    # Workbooks.Open 第三个位置参数固定 ReadOnly=$true。
    $workbook = $excel.Workbooks.Open($xlsPath, 0, $true)
    if (-not $workbook.ReadOnly) {
        throw '工作簿未以 ReadOnly 模式打开。'
    }
    if ($workbook.Worksheets.Count -ne 17) {
        throw "工作表数漂移：$($workbook.Worksheets.Count)"
    }

    $seenTitles = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; $sheetIndex++) {
        $sheet = $null
        $usedRange = $null
        try {
            $sheet = $workbook.Worksheets.Item($sheetIndex)
            $usedRange = $sheet.UsedRange
            $rows = [int]$usedRange.Rows.Count
            $columns = [int]$usedRange.Columns.Count
            if ([int]$usedRange.Row -ne 1 -or [int]$usedRange.Column -ne 1) {
                throw "UsedRange 不从 A1 开始：$($sheet.Name)"
            }
            $values = $usedRange.Value2
            $title = [string](Matrix-Value $values 1 1 $rows $columns)

            if ($sheet.Name -eq 'Details') {
                if ($title -ne 'Filename' -or $rows -ne 12 -or $columns -ne 2) {
                    throw "Details 工作表布局漂移：title=$title rows=$rows columns=$columns"
                }
                continue
            }
            if (-not $expectedSheets.Contains($title)) {
                throw "未知测量表完整标题：$title"
            }
            if (-not $seenTitles.Add($title)) {
                throw "重复测量表完整标题：$title"
            }
            $shape = $expectedSheets[$title]
            $expectedRows = [int]$shape[0]
            $expectedColumns = [int]$shape[1]
            $group = [string]$shape[2]
            if ($rows -ne $expectedRows -or $columns -ne $expectedColumns) {
                throw "测量表尺寸漂移：$title=$($rows)x$columns"
            }

            if ($title -like 'Temperature ramp - *') {
                $xColumns = @(7)
                $yColumns = @(1, 2, 3)
                $requiredHeaders = @{1='Storage modulus'; 2='Loss modulus'; 3='Tan(delta)'; 7='Temperature'}
            } elseif ($title -eq 'Flow ramp - 6') {
                $xColumns = @(2)
                $yColumns = @(1, 3, 6)
                $requiredHeaders = @{1='Stress'; 2='Shear rate'; 3='Viscosity'; 6='Normal stress'}
            } else {
                throw "未覆盖的测量表角色：$title"
            }
            foreach ($column in $requiredHeaders.Keys) {
                $actualHeader = [string](Matrix-Value $values 2 ([int]$column) $rows $columns)
                if ($actualHeader -ne $requiredHeaders[$column]) {
                    throw "字段名漂移：$title/col=$column/$actualHeader"
                }
            }

            $lastDataRow = 0
            for ($row = $rows; $row -ge 4; $row--) {
                $valid = $true
                foreach ($column in @($xColumns + $yColumns)) {
                    try {
                        [void](Finite-Number-Or-Throw (Matrix-Value $values $row $column $rows $columns) "$title/$row/$column")
                    } catch {
                        $valid = $false
                        break
                    }
                }
                if ($valid) {
                    $lastDataRow = $row
                    break
                }
            }
            if ($lastDataRow -ne $expectedRows) {
                throw "最后有效数据行漂移：$title/$lastDataRow"
            }

            $builder = [Text.StringBuilder]::new()
            for ($row = 4; $row -le $lastDataRow; $row++) {
                for ($column = 1; $column -le $expectedColumns; $column++) {
                    $number = Finite-Number-Or-Throw (
                        (Matrix-Value $values $row $column $rows $columns)
                    ) "$title/$row/$column"
                    [void]$builder.Append($number.ToString('R', $culture))
                    if ($column -lt $expectedColumns) { [void]$builder.Append("`t") }
                }
                [void]$builder.Append("`n")
            }
            $pointCount = $lastDataRow - 3
            if (-not $groupCurveCounts.ContainsKey($group)) {
                $groupCurveCounts[$group] = 0
                $groupPointCounts[$group] = 0L
            }
            $groupCurveCounts[$group] = [int]$groupCurveCounts[$group] + 1
            $groupPointCounts[$group] = [long]$groupPointCounts[$group] + $pointCount
            $curves.Add([pscustomobject]@{
                sheet_name = [string]$sheet.Name
                curve_id = $title
                used_range_rows = $rows
                used_range_columns = $columns
                data_start_row = 4
                point_count = $pointCount
                x_columns = @($xColumns)
                primary_y_columns = @($yColumns)
                group = $group
                data_sha256 = Sha256-Text $builder.ToString()
            })
        } finally {
            if ($null -ne $usedRange) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($usedRange) }
            if ($null -ne $sheet) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($sheet) }
        }
    }

    if ($seenTitles.Count -ne 16 -or $curves.Count -ne 16) {
        throw "测量曲线数漂移：$($curves.Count)"
    }
    $curvePointCount = [long](($curves | Measure-Object -Property point_count -Sum).Sum)
    if ($curvePointCount -ne 2094) {
        throw "同步曲线点数漂移：$curvePointCount"
    }
    $expectedGroups = @{
        'Temperature ramp 1' = @(5, 462)
        'Flow ramp 6' = @(1, 22)
        'Temperature ramp 3' = @(5, 1194)
        'Temperature ramp 4' = @(5, 416)
    }
    foreach ($group in $expectedGroups.Keys) {
        $expected = $expectedGroups[$group]
        if ($groupCurveCounts[$group] -ne $expected[0] -or $groupPointCounts[$group] -ne $expected[1]) {
            throw "曲线组统计漂移：$group"
        }
    }

    [pscustomobject]@{
        source_archive = 'Melting.zip'
        source_archive_sha256 = $archiveSha256
        member = $memberName
        member_sha256 = $memberSha256
        read_only = [bool]$workbook.ReadOnly
        workbook_sheet_count = 17
        curve_count = $curves.Count
        curve_point_count = $curvePointCount
        group_curve_counts = $groupCurveCounts
        group_point_counts = $groupPointCounts
        curves = @($curves)
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    if ($null -ne $zip) { $zip.Dispose() }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()

    if ($null -ne $tempDirectory -and (Test-Path -LiteralPath $tempDirectory -PathType Container)) {
        $resolvedTemp = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $tempDirectory).Path)
        $expectedLeafPrefix = 'tpu-standard-xls-audit-'
        if (
            -not $resolvedTemp.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not [IO.Path]::GetFileName($resolvedTemp).StartsWith($expectedLeafPrefix, [StringComparison]::Ordinal)
        ) {
            throw "拒绝删除越界临时目录：$resolvedTemp"
        }
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
