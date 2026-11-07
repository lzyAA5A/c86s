# 把 C86S 刷进掌控板 2.0.4（ESP32-D0WDQ6 / 8MB）。
#
# 注：同样的流程已经搬进管理器（tools\c86mgr.py 的「⚡ 一键烧录」/ `flash` 子命令），
#     这个脚本留着给不想开窗口的时候用，两者参数与顺序一致。
#
# 顺序：整片备份 -> 擦 swap 分区 -> bootloader/分区表/固件 -> DOS 镜像 -> 硬复位。
# 用 esptool 5.x 的连字符子命令；--flash-mode 必须是 dio（qio 在这块板上会
# rst:0x7 (TG0WDT_SYS_RST) 黑屏循环）。
#
#   .\flash.ps1                     # COM3, 刷 1.44MB 的 MS-DOS 5.0（默认）
#   .\flash.ps1 -Image dos211       # 换成 360KB 的 Compaq DOS 2.11（开机快，但 MINIGAME 起不来）
#   .\flash.ps1 -SkipBackup         # 备份已存在时跳过（省 2-3 分钟）
#   .\flash.ps1 -Restore            # 把备份整片写回去（恢复原厂 MINI-OS）

param(
    [string]$Port = 'COM3',
    [ValidateSet('dos211', 'cardputer86')][string]$Image = 'cardputer86',
    [string]$Esptool,
    [switch]$SkipBackup,
    [switch]$Restore
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$esptool = $Esptool
if (-not $esptool) {
    $cands = @()
    if ($env:PLATFORMIO_CORE_DIR) {
        $cands += (Join-Path $env:PLATFORMIO_CORE_DIR 'packages\tool-esptoolpy\esptool.py')
    }
    $cands += (Join-Path $env:USERPROFILE '.platformio\packages\tool-esptoolpy\esptool.py')
    $cmd = Get-Command esptool.exe, esptool.py, esptool -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd[0].Source }
    foreach ($p in $cands) { if ($p -and (Test-Path $p)) { $esptool = $p; break } }
}
if (-not $esptool) {
    throw '找不到 esptool：装了 PlatformIO 后跑 pio pkg install -g -p tool-esptoolpy，或用 -Esptool 指定完整路径'
}
$build = Join-Path $root '.pio\build\c86s'
$backupDir = Join-Path $root 'backup'
$backup = Join-Path $backupDir 'gankong204-full.bin'

if (-not (Test-Path $esptool)) { throw "找不到 esptool: $esptool" }
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

function Run-Esptool {
    # 参数名不能叫 $Args：那是 PowerShell 的自动变量，会把子命令吃掉
    # （症状是 esptool 报 "Missing command."）。
    param([string[]]$EsptoolArgs, [string]$What)
    Write-Host "== $What" -ForegroundColor Cyan
    $all = @('--chip', 'esp32', '-p', $Port, '-b', '921600') + $EsptoolArgs
    & $esptool @all
    if ($LASTEXITCODE -ne 0) { throw "$What 失败 (exit $LASTEXITCODE)" }
}

if ($Restore) {
    if (-not (Test-Path $backup)) { throw "没有备份文件可恢复: $backup" }
    Write-Host "把备份整片写回（8MB，约 4 分钟）..." -ForegroundColor Yellow
    Run-Esptool @('--before', 'default-reset', '--after', 'hard-reset',
                  'write-flash', '0x0', $backup) '恢复备份'
    Write-Host "已恢复原厂固件" -ForegroundColor Green
    return
}

if (-not $SkipBackup) {
    if (Test-Path $backup) {
        Write-Host "备份已存在: $backup（要重做请先删掉）" -ForegroundColor DarkGray
    } else {
        Write-Host "整片备份 8MB -> $backup（2-3 分钟）" -ForegroundColor Yellow
        Run-Esptool @('--before', 'default-reset', '--after', 'no-reset',
                      'read-flash', '0x0', '0x800000', $backup) '备份原 flash'
    }
}

# swap 区（0x310000+0x90000）在旧固件里是 littlefs，残留数据会让 wear_levelling
# 挂载到一个陌生布局；先擦干净，让新固件自己格式化。
Run-Esptool @('--before', 'default-reset', '--after', 'no-reset',
              'erase-region', '0x310000', '0x90000') '擦 swap 分区'

Run-Esptool @('--before', 'default-reset', '--after', 'no-reset',
              'write-flash', '--flash-mode', 'dio', '--flash-freq', '80m',
              '--flash-size', '8MB',
              '0x1000', (Join-Path $build 'bootloader.bin'),
              '0x8000', (Join-Path $build 'partitions.bin'),
              '0x10000', (Join-Path $build 'firmware.bin')) '写 bootloader/分区表/固件'

$img = Join-Path $root "data\$Image.img"
if (-not (Test-Path $img)) {
    throw ("找不到系统盘镜像 {0}`r`n" -f $img) +
          "本仓库不附带任何 DOS 磁盘镜像（版权原因）。请自备一个可引导的 DOS 软盘镜像，" +
          ("命名为 data\{0}.img（360KB 或 1.44MB）即可；几何参数取自镜像自己的 BPB，不用改代码。" -f $Image)
}
$size = (Get-Item $img).Length
Write-Host ("== 写 DOS 镜像 {0} ({1:N0} KB) 到 0x600000" -f $Image, ($size / 1KB)) -ForegroundColor Cyan
Run-Esptool @('--before', 'default-reset', '--after', 'hard-reset',
              'write-flash', '0x600000', $img) '写 DOS 镜像'

Write-Host ""
Write-Host "刷写完成。板子已经在 921600 上推画面了：" -ForegroundColor Green
Write-Host "    python tools\c86host.py --port $Port --scale 3" -ForegroundColor Green
