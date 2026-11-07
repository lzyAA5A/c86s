# 掌控板 2.0.4 上的 8086 PC —— 移植交付说明

> **说明**：本文是移植过程的原始记录，保留当时的写法。文中的 `F:\1231\...` 是作者机器上的
> 绝对路径，仅作历史参考；实际使用请看仓库根目录的 [README](../README.md)，命令都在仓库目录下执行。

**跑起来了**：掌控板（ESP32-D0WDQ6 / 8MB）上跑 8086 PC 模拟器，**画面从串口推给
电脑**，键盘从电脑发回板子，电脑端一个窗口当显示器 + 键盘。板上原来的 MINI-OS
已被替换。

```
┌──────────────────────┐  UART0 921600 (CP2104)   ┌────────────────────────┐
│ 掌控板 2.0.4         │ ──── 视频帧（RLE）─────► │ c86host.py 窗口        │
│ ESP32-D0WDQ6 240MHz  │ ◄─── 扫描码 / 命令 ───── │ 320x200 显示 + 键盘    │
│ 8MB GD25Q64          │                          │ ＋状态栏 / 日志 / 截图 │
└──────────────────────┘                          └────────────────────────┘
```

实测：**24.3 秒**从复位到 MS-DOS 5.0 的 `A:\>` 提示符，`dir` 能列出磁盘内容，
MINIGAME 的菜单能正常出现，吞吐约 27 万条指令/秒（≈ 一台 4.77MHz 的 IBM PC/XT）。

开机比早先的 DOS 2.11 + 128KB 版本（12.2 秒）慢一倍，原因不在模拟器而在**分页**：
256KB 客机共 64 个 4KB 页，页缓存只装得下 32 页，POST 顺序扫全内存必然淘汰，
每次淘汰是一次 4KB 擦+写（约 48ms）。这 12 秒是 DOS 5.0 的必要代价（见第六节）。

## 一、怎么用

### 开窗口（每次要用就这一条）

```
cd <仓库目录>
python tools\c86host.py --port COM3 --scale 3
```

窗口里：上面按钮（重连 / 复位机身 / Ctrl+Alt+Del / 重画全屏 / 粘贴文字 / 存截图 /
缩放 1-4 倍），中间是 320x200 的画面，下面是板子日志，最下面是状态栏
（fps、客机内存、堆、TEXT/GFX、丢帧、坏帧数）。

**不需要装任何东西**：串口走 ctypes 直接调 Win32 API（这台机器 pip 源返回 403，
装不了 pyserial），画面走 tkinter + 自写 PNG 编码（zlib 是标准库）。

### 从零重建

```powershell
$env:PLATFORMIO_CORE_DIR='F:\1231\_tmp\pio-core'
pio run -e c86s

cd <仓库目录>
.\flash.ps1                      # COM3 + MS-DOS 5.0 (1.44MB)，默认
.\flash.ps1 -Image dos211        # 换成 Compaq DOS 2.11 (360KB)：开机 12 秒，但 MINIGAME 起不来
```

### 换系统盘

镜像就是一个裸扇区分区（`disk` @ 0x600000，2MB）。换盘 = 把另一个 img 写进去：

```powershell
esptool -p COM3 -b 921600 write-flash 0x600000 data\cardputer86.img
```

几何参数从镜像自己的 BPB 读，不用改代码（360K → 40/2/9，1.44M → 80/2/18）。

## 二、做了什么

模拟器内核（`cpu.cpp` / `video.cpp` / `disk.cpp` / `ports.cpp` / `guest_memory.cpp` …）
**没有重写**，只换掉了"机器层"——原来那层是 M5GFX 屏幕 + Cardputer 键盘矩阵 +
FatFS 镜像 + I2S 喇叭：

| 原来（Cardputer / ESP32-S3） | 现在（掌控板 / ESP32） |
| --- | --- |
| LGFX sprite → ST7789 240x135 | 4bpp 打包 + 逐行 RLE → UART0 |
| Cardputer 键盘矩阵 | 电脑发来的 PS/2 set-1 扫描码 → 模拟 8042 |
| FatFS + 磨损均衡里挂 IMG 文件 | 裸 flash 分区当磁盘（无文件系统） |
| I2S 喇叭 | BEEP 帧，电脑侧播 |
| TFT 上的设置菜单 | 电脑端窗口的按钮 |

固件 **402 KB**（占 3MB app 分区 12.8%）、静态 RAM **96 KB**（29%），
比 S3 版的 2.0 MB 小了一个数量级——代价是不再有板上显示和板载键盘。

视频通道的几个关键设计：

* **4bpp 打包**：320x200 一字节一像素的 CGA 序号压成两位一字节，一行 160 字节。
* **逐行 RLE**：`c<0x80` 字面量跑、`c>=0x80` 重复跑。整屏文字 32000 → 6.9 KB。
* **代数 + 签名去重**：每行记"哪个刷新代发过"和内容签名，没变的一个字节都不发。
  停在 `A:\>` 时总线占用接近 0，敲一个字符只发那一行。
* **预算分帧**：一帧最多 12 KB，装不下留到下一帧。整屏刷新被切成 2-3 帧也不会漏行
  （用代数标记，不是布尔开关——早期版本用一个 `want_full` 布尔，切分帧后剩余行
  再也不会重发，画面会残）。

键盘：电脑端把按键翻译成 PS/2 扫描码（含 Shift 的 make/break 配对、方向键的
0xE0 前缀），板子塞进 8042 的 0x60 数据口并触发 IRQ1，客人机的 BIOS INT 9 处理。
一次键盘轮询只交付一个字节（模拟器只有一个数据寄存器），所以打字上限约 50 键/秒。

## 三、踩到的坑（都修了，记在这免得下次重踩）

1. **原来的 80 列路径是坏的**。`gb_sdl_font4x8.h`（Pierre Sarrazin 的 ISO-8859-1
   4x8 字模）用 **0 表示笔画**，和 `gb_sdl_font8x8.h` 相反；`SDLprintChar4x8` 按
   1=笔画画，所以 80 列模式整屏是实心块挖空字。默认走 8x8 所以从没人发现。
   掌控板这版必须用 4x8（8x8 只能显示 80 列里的 40 列，DOS 到 41 列就废了）。
2. **小消息被视频通道饿死**。原来消息要求"发送缓冲一次能装下整条"，而视频路径总把
   环形缓冲灌到只剩 16 字节 → 超过 16 字节的日志/trace 永远发不出去。改成带游标的
   分块推送（视频帧仍优先整帧发完，避免把消息插进视频帧中间导致对端错帧）。
3. **`part_read()` 固定读 512 字节**，我只给了 16 字节栈缓冲 → 栈写穿 →
   `rst:0xc (SW_CPU_RESET)` 开机崩溃循环。
4. **分区表 CSV 必须 ASCII**。构建脚本按系统 locale（GBK）读它，中文注释直接
   `UnicodeDecodeError`，且错误发生在链接之后，看起来像链接失败。
5. **PowerShell 参数不能叫 `$Args`**（自动变量），否则 esptool 收到空命令报
   "Missing command"。另外 `flash.ps1` 存成 UTF-8 无 BOM 会被 PowerShell 5.1
   按 GBK 读成乱码 → 解析失败，所以文件带 BOM。
6. 串口整片读 8MB 会偶发 `digest mismatch` / `Serial data stream stopped`，
   拆成 512KB 分块就稳（备份就是这么拿到的）。
7. `-Os` 编译比 `-O2` 慢近一倍（9.6ms/批 vs 5.6ms/批）——这种大 switch 解释器
   不要迷信"小就是快"。

## 四、诊断通道（黑屏时救命）

串口被协议占满了，所以客人机的状态通过 LOG 帧回传，窗口下方就能看见：

```
trace F000:FA22 opF3AA vid3 i190 ins71989 cpu5597us vb4047 fb4039 io136/0/0 lba5 geo56 mnt1/00 ih13=55 ih16=45997 ih19=1
scr r2 [A>                                      ]
```

* `CS:IP op...` 客人机停在哪、下一条指令是什么（`CD 16` = 在等键盘）
* `cpu=…us` 2000 条指令耗时（性能探针）
* `vb` 显存非零字节 / `fb` 帧缓冲非零像素 —— 一眼分清"客人机没写"和"我们没画"
* `io=读/写/失败 lba` 磁盘扇区计数，`geo` 几何查询次数，`mnt/drv` 挂载状态
* `ih13/ih16/ih19` INT 13h/16h/19h 次数
* `scr rN [...]` 最后一行非空文本的内容 —— DOS 停在哪个界面直接读出来

另外三个小工具：

```
python tools\rawmon.py 115200 6      # 裸串口（panic / bootloader 日志永远在 115200）
python tools\poke.py --keys "dir\r"  # 敲几个键再抓图，比较画面是否在变
python tools\boottime.py             # 复位客人机并计时到 A>
```

## 五、文件在哪

```
F:\1231\_tmp\C86S\
├── platformio.ini            env:c86s（esp32dev / DIO@80MHz / 8MB / -O2）
├── partitions_c86s.csv       8MB 布局（ASCII，别加中文）
├── flash.ps1                 备份 / 刷写 / 恢复（.\flash.ps1 -Restore）
├── README.md                 工程本身的技术文档（协议、取舍、限制）
├── backup\                   原固件 0x0-0x3A0000 的备份（3.7MB）+ 说明
├── docs\screenshot-dos-3x.png    实拍：DOS 里 dir
├── docs\screenshot-minigame.png  实拍：MINIGAME 菜单（80×25 全屏版）
├── docs\games80\g1..g7.png       实拍：七个游戏的 80×25 版
├── docs\screenshot-shift-fixed.png 实拍：敲 "!21,9.0" 逐字正确（Shift 修复验收）
├── data\dos211.img           Compaq MS-DOS 2.11 启动盘 360KB
├── data\cardputer86.img      MS-DOS 5.0 启动盘 1.44MB
├── src\main.cpp              setup/loop + 客人机诊断探针
├── src\emu\                  模拟器内核（从 CardPuter86 原样复制）
├── src\serial\               新写的串口机器层
│   ├── c86_link.cpp          UART 收发 / 帧 / RLE / 脏行去重 / 键盘队列
│   ├── cardputer_display.cpp 同名 API，落点换成串口
│   ├── cardputer_storage.cpp 裸分区扇区读写 + 写缓存
│   ├── cardputer_speaker.cpp → BEEP 帧
│   ├── cardputer_rtc.cpp     CMOS 时钟
│   └── cardputer_modem.h     CARDPUTER86_NO_MODEM 空实现
└── tools\
    ├── c86host.py            电脑端窗口（零依赖）
    ├── fat12.py              软盘镜像 list/space/put/del（put 带 --replace）
    ├── poke.py               敲键 + 抓图（端到端验键盘）
    ├── bootlog.py            抓启动日志（含客机内存用了几个缓存页）
    ├── boottime.py           量「复位→盘符提示符」的耗时
    ├── rawmon.py             115200 抓 ROM panic（只在复位/崩溃循环时用）
    ├── _keytest.py           键盘扫描码单元测试（不需要板子，11 项）
    ├── gameshots.py          逐个进 MINIGAME 的游戏并截图（80×25 布局验收）
    └── _pipeline_test.py     视频管线单元测试
```

原来的 CardPuter86 工程（`F:\1231\_tmp\CardPuter86`）**没有改动**，它那份是给
M5Stack Cardputer（ESP32-S3）用的。

## 六、已知限制 / 下一步

* **MINIGAME 的七个游戏已改成 80×25 全屏**（菜单框 2..77 列、扫雷 24×16 格 50 雷、
  五子棋 15×15、方块井 14×22、吃豆人 41×23 镜像迷宫、迷宫 79×23、恐龙地面降到第
  21 行）。源码在 `F:\1231\cardputer-term\dosgames\minigame.cpp` + `gomoku.h`，
  Digital Mars `dmc -msd -w -I. minigame.cpp -o MINIGAME.EXE` 编译，
  `tools\fat12.py put --replace` 替换进镜像。实拍见 `docs\games80\`。
  仍是 40×16 的是三个独立程序 `SNAKE.EXE` / `MINE.EXE` / `G2048.EXE`（另有源码
  `snake.c` / `mine.cpp` / `game2048.c`），菜单里的七个不受影响。
* **80 列下用的是 4×8 字模**，比 DOS 命令行的 8×8 挤（DOS 自己用 8×8，所以
  命令行更清楚）。这是 320 像素宽放 80 列必然的代价；要字漂亮就只能是 40 列。
* **图形模式是近似色**。文字模式（DOS 命令行）用标准 CGA 16 色，正确；
  mode 4/5/6 里模拟器往帧缓冲写的是 2 位序号，落成了调色板前 4 项，
  和真实 CGA 的子调色板选择不一致。跑游戏能看出偏色。
* **磁盘写入不落盘**。64 扇区 RAM 写缓存，掉电即失；读是 flash 直读。
  要持久化得加磨损均衡写回（`disk` 分区 2MB，够 1.44MB 镜像 + 写回区）。
* **游戏会慢**。全屏 RLE 后 6.9KB / 75ms，静止画面无压力，但每秒多次全屏刷新的
  游戏会掉到个位数 fps。要提速的两个方向：串口升到 2M（CP2104 支持，需实测误码），
  或者只传"变化块"（现在是按行）。
* **没有声音**（BEEP 帧已发到电脑，客户端还没播）。
* **开机 24 秒**。256KB 客机共 64 个 4KB 页，页缓存只拿得到 32 页（剩下的 DRAM
  被静态 96KB + 帧缓冲 62.5KB 占掉），POST 顺序扫全内存必然淘汰，每次淘汰是一次
  4KB 擦+写（约 48ms）。提高 `-DG86_CACHE_PAGES_MAX` 也救不回来：要零换页得
  64 页 = 256KB，超过整块 DRAM 能给的量。真想要 12 秒开机只能回到 128KB 客机，
  而 128KB 装不下 DOS 5.0（IO.SYS 自重定位就顶出 128KB 顶端，实测）。
* **客机内存默认 256KB**，这是 MS-DOS 5.0 的下限（IO.SYS 33430 + MSDOS.SYS 37394
  + COMMAND.COM 47845 ≈ 118KB）。换回 `-DG86_FORCE_RAMKB=128` 只能跑 DOS 2.11，
  且 MINIGAME 这类程序调用的 INT 21h 功能在 DOS 2.11 里不存在 —— 菜单根本不出来。
* **Shift 记账按扫描码，不按 keysym**（已修）。Windows 上按 Shift+1 得到 `!` 时
  keysym 是 `exclam`，若先松 Shift 再松 1，松开事件的 keysym 会变回 `1`；老代码按
  keysym 配对就配不上 → 自动 Shift 的 break 码（0xAA）永远发不出去 → 客人机一直
  认为 Shift 按着 → 之后数字全变上档符号（1 变 `!`、`,` 变 `<`）。现在
  `c86host.py` 的 `auto_shift` 存扫描码，并新增 `release_all_modifiers()`
  （窗口失焦 / 连接时补发 0xAA 0xB6 0x9D 0xB8）和界面上的「松开修饰键」按钮。
  回归测试 `python tools\_keytest.py`（11 项，含"Shift+1 后先松 Shift"这条）。
  端到端实测：敲 `!21,9.0` 屏幕逐字显示 `A:\>!21,9.0`（修复前 `!` 之后全变上档）。
* **8042 状态字节被命令字节污染**（已修 —— 「复位之后打不了字」的真凶）。
  `ports.cpp` 原来把"往 0x64 写命令"直接存进同一个字节，而 0x64 是状态口：
  POST 的自检命令 0xAB 的 bit1 恰好是 1，凭空伪造出"输出缓冲满"，全工程又没有任何
  一处清它 → 客人机认为键盘控制器永远有新数据，判定键盘异常，复位后一个键都进不去。
  现在状态位由仿真器独占（`case 0x064` 只保留真正由投递置起的 bit1），读 0x60 时按
  真机语义清 OBF，`emu_reset_guest()` 把 0x60 / 0x64 / 键盘队列一起复位。
* **键盘投递必须按 OBF 流控**（已修 —— 「打字丢字符 → Enter / Q 都不灵」的真凶）。
  `kbd_deliver_one()` 原来每 10ms 无条件覆盖 0x60：256KB 分页下客人机一次键盘中断
  要跑十几毫秒，还没被读走的扫描码就被冲掉 —— `minigame` 变成 `minig`，Enter 送出
  的是一条无效命令，表现就是"进不去游戏 / 退不出来"。现在等客人机读走（读 0x60 清
  OBF）再送下一个，另加 0.5 秒安全阀防死锁。实测 20 字符（40 字节）在 0.55s/字 与
  0.06s/字 两种节奏下都是 `push=del=read=40`、零丢失；端到端四项全过：
  菜单出现 / Enter 进游戏 / Q 退回菜单 / 复位后 `A:\>123` 逐字正确。
  诊断：`emu_kb_trace()` 只在真丢字节时才打 `kb push= del= read= force= drop=`，
  `python tools\_kbdrop.py COM3 <字符串>` 自动打一串并给出五个计数器的增量。
* **不要相信 Tk 的 `event.state` 位**（已修 —— 「GUI 里怎么打都没反应」的真凶）。
  这台机器上 Tk 给每个键盘事件的 `state` 恒为 `8`，而 X11 语义里 `0x8` 是 Mod1/Alt，
  `GetAsyncKeyState` 却显示 Shift/Ctrl/Alt 全都没按着 —— 这个位是假的。旧代码
  `alt = ... or bool(state & 0x8)` 把每个键都当成 Alt+键 发出去，客人机对 Alt 组合
  一律吞掉，屏幕上一个字都不出。而同一个键用 `tools\_kbdrop.py` 直接发扫描码完全
  正常，所以极易误判成固件问题。现在修饰键只认真正收到的 `Shift_L`/`Control_L`/
  `Alt_L` 事件，`state` 只留在 `--keydebug` 输出里。
  验证：用 `PostMessage` 向窗口投递真实键盘消息后，画面出现 `A:\>abc123`；
  `python tools\_keytest.py` 12/12 通过（含"幻影 state=8 不当作 Alt"）。
* **排查 GUI 输入失效**：`python tools\c86host.py --port COM3 --keydebug` 会把按键事件
  与每次串口写打到 stderr；`send()` 不再裸 `except: pass`（写失败会报出来）。
  注意 `SendInput`/`SendKeys` 在焦点被抢的环境里可能根本到不了窗口，用
  `F:\1231\_tmp\keytest_win3.ps1`（PostMessage 版）验证更可靠。

## 七、想换回原来的固件

```powershell
cd <仓库目录>
.\flash.ps1 -Restore      # 把 backup\gankong204-full.bin 整片写回 0x0
```

备份只覆盖 0x000000-0x3A0000（3.7MB），正好是这次改写过的全部区域；
0x400000-0x600000 的原厂 GUIX 字模**没动过**，0x600000-0x800000 原本就是全 FF。

## 八、按键修复（「无法输入」「. 变 <」）

用户原话：「无法输入,或者. 会变成<> 其它的符号也是同理」。

两条现象是同一个错误的两个面，都在电脑端 `tools\c86host.py` 的键盘处理里：**都靠
「按下」「松开」两个事件配对销账**，而配对在真机上会丢：

1. **`self.pressed` 按 keysym 去重** —— 原本用来挡 Windows 的自动重复。松开事件一旦
   认不出（中文 IME 下 `event.char` 常为空、keysym 变成 `??`），那条记录就永远删不掉，
   此后**同一个键被静默吞掉**：`无法输入 .` 就是它。
2. **`auto_shift` / `auto_ctrl` / `auto_alt` 把修饰键的 break 挂在松开事件上** ——
   松开事件认不出时 `0xAA` 永远发不出去，客人机一直认为 Shift 按着：`,` `.` 于是
   全变成 `<` `>`（同理 `1` 变 `!`）。这正是最早那个 bug 的残留形态。

修法（`on_key_press` / `on_key_release`）：**每次按键自带一圈修饰键，立即闭合**——
上档字符发 `2A → 键 → AA`，Ctrl/Alt 组合同理；松开时只发键自己的 break。不再有任何
跨事件的账要销。同时**取消 keysym 去重**：真实的 8042 本来就靠 make/break 表达状态，
重复的 make 是有意义的（DOS 自己判断长按）。

验证：

* `python tools\_keytest.py` —— **15/15**。新增三项专打真机失效模式：
  「release 认不出也不卡 Shift」「release 认不出后同键仍能再发」「Alt+',' 之后逗号干净」。
* `python tools\_livekeys.py COM3` —— 真串口 + 真实 App 事件处理。注入
  `.,;/=-1!2@'"3#4$5%6^`（20 字，含全部上档符号），**每个 release 都故意带认不出的
  keysym**，客人机屏幕逐字回显 `A:\>.,;/=-1!2@'"3#4$5%6^`。
* `python tools\_entcheck.py` —— 真帧缓冲截图逐帧确认：菜单 **Enter** → SNAKE 运行
  （`WASD/arrows move P pause Q quit`）→ **Q** 回菜单 → `7`+Enter → MAZE
  （`MAZE 1 moves 0 0.0s Q=quit`）→ 迷宫里 **Q** 回菜单。**「Enter 进不去 / Q 退不出」
  在现固件上不复现**（`minigame.cpp:2160` 明确处理 `al == 0x0D`，`is_quit()` 认 ESC/Q），
  当时的现象是旧固件丢键造成的。

## 九、这一轮学到的排查纪律

* **本机不能用 PostMessage 伪造键盘输入**：C86S 窗口挂着中文 IME（`MSCTFIME UI` /
  `IME` 窗口），投进去的 `WM_KEYDOWN`/`WM_CHAR` 不会变成 `<KeyPress>`
  （`--keydebug` 的 stderr 里 0 条 KeyPress，即使窗口已确认在前台）。要端到端验键盘，
  就用 `tools\_livekeys.py`（喂 App 的事件处理方法 + 真串口）或纯串口 `Rig`。
* **截图不能用 `CopyFromScreen`**：会被前台别的程序挡住（实测抓到过 mPython 的积木界面）。
  用 `PrintWindow(hwnd, hdc, 2)`（`F:\1231\_tmp\shotwin.ps1`）；抢前台用
  `AttachThreadInput` + `SetForegroundWindow`（`F:\1231\_tmp\front.ps1`）。
* **`App.append_log` 走 `root.after(0, ...)` 跨线程**，脚本里没有 mainloop 时会静默丢日志；
  诊断脚本应直接截获 `handle_frame` 的 `T_LOG` 载荷。
* **客人机每键间隔 < 0.2 秒会吞字**（实测 0.08s 丢、0.4s 稳）；trace 的 `scr` 行还会
  滞后、被满屏重绘饿死 —— 判断游戏状态要用真帧缓冲截图，别只看 `scr` 末行。

## 十、配套工具：C86S 管理器（一键启动 + 虚拟软盘编辑）

`tools\c86mgr.py`，纯 tkinter + 标准库（本机装不了第三方包，pip 走不通），
把「起模拟器」和「改板子上的虚拟软盘」合到一个窗口里。

### 一键启动

| 方式 | 命令 |
| --- | --- |
| 双击脚本 | `F:\1231\_tmp\C86S\start-c86s.cmd`（优先 `pythonw`，不弹黑框） |
| 管理器按钮 | 串口选 COM3、缩放选 3 → 点「▶ 启动模拟器」；「■ 停止」关掉它腾出串口 |

管理器里还有「重连 / 复位机身 / Ctrl+Alt+Del / 重画全屏」等操作全部在模拟器窗口自己的
工具条上，管理器只管进程生命周期。

### 虚拟软盘（板上 `0x600000`，1.44MB FAT12）

* **从板子下载…**：分段读（512KB 一段，`230400` 波特）写成本地 `.img`。
  整片一次读会 `digest mismatch` / `Serial data stream stopped`，所以必须分段。
* **上传到板子**：先校验大小 = 1474560 字节，再 `esptool write-flash 0x600000`
  （`921600` 波特，esptool 自己会 `Hash of data verified`）。
* **导出选中… / 导入文件… / 删除选中**：直接改本地 `.img`（`tools\fat12.py` 的
  `get/delete/put`），改完标题栏出现 `*`，**必须点「上传到板子」才算写到硬件**。
  文件名按 8.3 规范规整（`save83`），超长名会被截断。
* **刷新列表**：重新解析本地镜像（40 个文件那套）。

上传/下载都会先问一句要不要关掉模拟器 —— 串口是独占的。
命令行等价：

```
python tools\c86mgr.py dl COM3 out.img     # 下载
python tools\c86mgr.py up COM3 in.img      # 上传
python tools\c86mgr.py ls in.img           # 列文件 + 空闲空间
```

### 实测

* 下载 → 与 `data\cardputer86.img` 逐文件比对：40 个文件内容全同，无增无缺。
* 改盘往返：写入 `MGRTEST.TXT`(38B) → 上传(19.8s) → 下载 → 列表里出现；
  再传回原镜像 → 该文件消失、空闲回到 736KB。
* `tools\_starttest.py`：脱离窗口直接调 `start_emulator()`，起得来、6 秒后仍存活，
  说明「启动模拟器」按钮走的这条路是通的。
  （Tk 的 ttk 控件不吃 `PostMessage` 伪造的鼠标点击，按钮回调没法从外部点，只能这样验。）


## 十一、一键烧录与串口码率（2026-11-07）

### 一键烧录

管理器里变成一条按钮，不用再记 esptool 参数：

```
固件烧录（bootloader 0x1000 / 分区表 0x8000 / 应用 0x10000）
  固件目录 F:\1231\_tmp\C86S\.pio\build\c86s   已编译 ✓，firmware.bin 395 KB（11-07 13:01）
  [✓] 同时写软盘镜像（用上面的本地镜像）  [ ] 先整片备份（没有备份时）
  (⚡ 一键烧录)  (写回备份（恢复原厂）)
```

点下去的顺序（和 `flash.ps1` 完全一致，已被这轮实测证明）：

1. 模拟器开着就先关掉腾串口（会问一句）
2. `[可选]` 整片备份 8MB → `backup\gankong204-full.bin`（只在没有备份时做，约 2-3 分钟）
3. 擦 `swap` 分区 `0x310000` + `0x90000`（旧固件那里是 littlefs，残留会让新固件挂载到陌生布局）
4. 写三件套：`0x1000 bootloader.bin`、`0x8000 partitions.bin`、`0x10000 firmware.bin`
   —— **必须** `--flash-mode dio --flash-freq 80m --flash-size 8MB`；用 qio 会 `rst:0x7 (TG0WDT_SYS_RST)` 黑屏循环
5. `[可选]` 写软盘镜像到 `0x600000`
6. 硬复位

固件没编译就直接点，会告诉你缺哪个文件并给出编译命令，不会烧到一半才失败。
命令行等价：

```
python tools\c86mgr.py flash COM3                    # 固件 + 软盘镜像
python tools\c86mgr.py flash COM3 --no-image         # 只烧固件
python tools\c86mgr.py flash COM3 --baud 460800 --backup
python tools\c86mgr.py restore COM3                  # 整片写回备份
```

实测：只烧固件约 35 秒（erase 576KB + 写 395KB，每步 `Hash of data verified`）；
带 1.44MB 软盘镜像再 +20 秒；写完后 `esptool verify-flash 0x600000 data\cardputer86.img`
→ `Verification successful (digest matched)`。

### 三个码率

| 旋钮 | 默认 | 作用 |
| --- | --- | --- |
| 链路码率 | 921600 | 模拟器画面/键盘那条 UART（`c86host.py --baud`） |
| 烧录/上传 | 921600 | `esptool -b`（写固件、写软盘） |
| 读取 | 230400 | 下载软盘用；再高容易出现 `digest mismatch` / `Serial data stream stopped` |

链路码率是**真的能运行时切**的：固件新增 `C86_CMD_SETBAUD 0x05`（4 字节小端绝对速率），
`c86_link_set_baud()` 会丢掉还没发完的旧帧、`Serial.flush()` 等 TX 排空、
`Serial.updateBaudRate()` 切 UART、清接收状态机，然后在新速率上补发一个 HELLO 让 PC 确认。

PC 侧（`c86host.py`）的连接流程：

1. `resolve_baud()` 先按期望速率探 0.6 秒，不通就把 `BAUD_PROBE`
   (921600/460800/230400/115200) 逐个探一遍 —— 因为板子不记得"上次是谁把它改走的"
2. 找到当前速率后发 `SETBAUD(想要的值)`，等 150 ms，用新速率再确认 HELLO
3. 确认不到（老固件没这条指令 / 线材撑不住）就退回原来那个速率，并写一行日志说明
4. **窗口关掉时把板子交回 921600** —— 否则板子停在 460800 上，下一个人按 921600
   连它"一句话都不应"，看起来像板子死了

实测：`921600 → 230400 → 921600`、`921600 → 460800 → 921600` 双向都通；
460800 下文字模式仍是 24 fps（`COM3 @460800  24 fps  guest 256KB`），静止画面
只有 ~1.8 KB/s，降速对文字界面几乎没影响。

### 这轮踩到的两个坑

1. **收尾硬复位不能靠再起一个 esptool**。最初写成"烧完用
   `flash-id --before no-reset --after hard-reset` 收尾"，结果前一条命令是
   `--after no-reset`（停在 flasher stub 里），新起的 esptool 连不上，
   报 `A fatal error occurred: Failed to connect to ESP32: No serial data received.`
   —— 固件其实已经写完了，只是最后这一步失败并抛异常，看起来像整个烧录失败。
   正解：让**最后一条写命令自己**带 `--after hard-reset`。
2. **"已经是这个速率"要跟当前速率比，不能跟编译期常量比**。第一版固件写的
   `if (baud == C86_BAUD) return;`，现象是 `921600 → 230400` 能切、
   `230400 → 921600` 静默失败（既不切也不回 HELLO，PC 侧只能判"板子没接受"）。
   改成静态变量 `cur_baud` 跟踪实际值后正常。


## 十二、Shift 锁死（2026-11-14 二次修复：左右 Shift 是两个独立标志位）

用户原话：「shift锁死还是存在，而且很容易出现」。这一轮找到了真凶，之前三次
修复都只清了一半状态。

### 真凶：BIOS 的 Shift 标志位分左右

BIOS 键盘标志字节 `0x40:0x17` 里，**左 Shift 是 bit1、右 Shift 是 bit0，两个
独立位**。而先前所有修复都只发 `0xAA`（左 Shift 抬起码）：

- 按**右** Shift 打一个上档字符，随后那一次 release 如果丢了（本机中文 IME 的
  `MSCTFIME UI` 会吃掉一部分 keyup；窗口失焦瞬间松开的键更收不到），客人机的
  bit0 就永久挂着；
- 之后每个未上档的 `1` 都被翻译成 `!`、`,` 变 `<`、`.` 变 `>`；
- 我们补的 `0xAA` 清的是 bit1，**怎么补都清不掉 bit0** —— 所以「修了还在」，
  而且「很容易出现」：习惯按右 Shift 的人几乎必然踩到。

### 这一版的设计：不再信任 press/release 记账

1. 上档字符 = `Shift⊗ → 键 → Shift⊕` 原子三连，不看记账（记账错了也不影响结果）；
2. 未上档的可打印字符 = 先发 `0xAA` **和** `0xB6`（左右各一个 break，幂等），
   再发键本身 —— 客人机残留的 Shift 在这一步被清掉；
3. 记账与「系统给的字符」冲突时以字符为准，并补发丢失的 break：
   可打印且未上档 ⇒ Shift 没按着；裸字母（keysym 与 char 是同一个字母）
   ⇒ Ctrl/Alt 没按着（有 Ctrl 时字母是控制字符，不会是裸字母）；
4. 方向键/功能键**不动**修饰键状态 —— Shift+方向键、Ctrl+方向键保持可用；
5. 兜底看门狗：记账认为按着、却 5 秒没有任何键盘事件 ⇒ `tick` 里补一次 break
   （真人按住修饰键 5 秒不碰别的键极少见，而真按着时下一个上档字符自带一圈
   Shift，打字结果不受影响）；
6. `<FocusOut>` 也补一遍 break（原先只有 `<FocusIn>`）—— IME 候选窗弹出时
   keyup 进了别人的窗口；
7. 状态栏出现「自愈 N」说明这一轮补发了几次释放码，是诊断入口。

### 验证

- `python tools\_keytest.py` → **22/22 通过**，新增 7 项直接针对本 bug：

  | 用例 | 期望 |
  |---|---|
  | 释放事件丢失后敲 `1` 仍是 `1` | `2A 2A 02 AA  AA B6 AA B6 02` |
  | **右** Shift 释放丢失后 `.` 仍是 `.` | `36 AA B6 AA B6 34`（断言必须含 `B6`） |
  | 客人机残留 Shift 时 `,` 仍是 `,` | `AA B6 33` |
  | Ctrl 释放丢失后字母仍是字母 | `9D AA B6 1E` |
  | 真 Ctrl+字母不被误清 | `1D 2E`（char 是控制字符，不做自愈） |
  | 看门狗 5s 无事件补 break | 含 `AA B6`，且 `shift_down` 归零 |
  | 按住 Shift + 方向键不动 Shift | `4B* CB*`（不补 break） |

- 新脚本 `tools\_shiftprobe.py`（真机探针）：绕过 GUI 直接发原始扫描码，把
  「快节奏（0 间隔）」和「慢节奏（0.30s）」对比着测，用来判读客人机到底是
  「本身处理不好」还是「节奏太快被吞」：

  ```
  python tools\_shiftprobe.py COM3
  python tools\_shiftprobe.py COM3 --raw 2A,02,AA,04    # 单发一串自己看
  ```

### 注意

- 本轮验证时掌控板不在 USB 上（COM3/CP210x 消失，COM5 的 CH9102 也被别的
  程序占着），所以以上是**客户端单测**结论；插回板子后建议按
  `_shiftprobe.py` → `_livekeys.py` → 手动打字的顺序复测一遍。
- 客户端改动**不需要重刷固件**。
- 界面窗口若开着，必须**重启**才会用上新代码（旧进程还在内存里跑老逻辑）。

