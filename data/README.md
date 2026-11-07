# data/

系统盘镜像放这里，**不入库**（版权原因，见仓库根目录 NOTICE.md）。

需要一个**可引导的 DOS 软盘镜像**（360 KB 或 1.44 MB），命名成：

    data\cardputer86.img     # flash.ps1 默认用它
    data\dos211.img          # flash.ps1 -Image dos211 用它

写完直接 `.\flash.ps1 -Port COM3`。几何参数取自镜像自己的 BPB，不用改代码。
本仓库不提供任何 DOS 镜像。
