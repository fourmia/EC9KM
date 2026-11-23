#!/usr/bin/env python3
"""
ECMWF IFS 0.1° 共享定义 —— 参数、层、步长、代理的唯一来源
============================================================

`download_ecmwf.py`（单次下载 + 后处理）与 `cron_fetch_ecmwf.py`（业务化
GRIB2 下载）都从这里取定义。任何参数或步长变更只改本文件，避免两处漂移。

实测依据（GCS index 文件 + eccodes 解析，2026-10-22 00z / 2026-11-03 00z）：
  - 地面层 : 45 个 shortName
  - 压力层 : 10 个变量 × 14 层（10–1000 hPa）
  - 00z/12z: 0–144h/3h + 150–360h/6h = 85 步
  - 06z/18z: 0–90h/3h = 31 步（官方文档所称 1h/91 步不存在）
"""

import os
import sys
from typing import Optional

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 地面层参数（sfc）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心气象变量（默认下载）
PARAMS_SFC_MET = [
    "2t",     # 2米气温 (K)
    "2d",     # 2米露点温度 (K)
    "10u",    # 10米纬向风 (m/s)
    "10v",    # 10米经向风 (m/s)
    "10fg",   # 过去3小时最大10米阵风 (m/s)，stepType=max
    "mn2t3",  # 过去3小时最低2米气温 (K)
    "mx2t3",  # 过去3小时最高2米气温 (K)
    "msl",    # 海平面气压 (Pa)
    "sp",     # 地面气压 (Pa)
    "tp",     # 累计总降水 (m)
    "tcc",    # 总云量 (0-1)
    "lcc",    # 低云量 (0-1)
    "mcc",    # 中云量 (0-1)
    "hcc",    # 高云量 (0-1)
    "mucape", # 最不稳定对流有效位能 (J/kg)
    "skt",    # 皮温 (K)
]

# 辐射变量（原始为累计量 J/m²，需差分转 W/m²）
# 注：0.1° tier 暂未提供 tisr/ssrdc/strdc/fdir
PARAMS_SFC_RAD = [
    "ssrd",   # 地面向下太阳短波辐射（GHI 分量基础）
    "strd",   # 地面向下长波（热红外）辐射
    "ssr",    # 地面净短波辐射
    "str",    # 地面净长波辐射
    "ttr",    # 大气层顶净长波辐射（OLR，累计，向外为负）
]

# 扩展变量（补齐 45 个 SFC 参数，--extra 启用）
PARAMS_SFC_EXTRA = [
    "100u", "100v",                          # 100米风场（风能行业）
    "cp", "sf", "ptype", "tprate",           # 降水细分
    "tcwv", "tclw", "tciw", "tcrw", "tcw",  # 整层水汽与云水
    "sd", "rsn", "asn", "ro", "vsw", "sot",  # 陆面与土壤
    "sithick", "zos", "lsm",                 # 海洋与静态场
    "ewss", "nsss",                          # 湍流通量（累计量）
    "sve", "svn",                            # 海浪
]

# 全部 45 个（顺序即 index 文件校验顺序无关，仅保证集合完整）
PARAMS_SFC_ALL = PARAMS_SFC_MET + PARAMS_SFC_RAD + PARAMS_SFC_EXTRA

# 辐射变量分类
RAD_ACCUM_VARS = set(PARAMS_SFC_RAD)          # 累计量，需差分
RAD_DOWNWARD_VARS = {"ssrd", "ssr", "strd"}   # 向下辐射，物理上不可为负

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 压力层参数（pl）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PARAMS_PL = ["z", "gh", "t", "u", "v", "w", "q", "r", "d", "vo"]
# z=位势, gh=位势高度, t=温度, u/v=风场, w=垂直速度
# q=比湿, r=相对湿度, d=散度, vo=相对涡度

# 14 个标准压力层（hPa），与 GCS index 文件一致
LEVELS_PL = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50, 10]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 预报步长
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主批次（00z/12z）：0–144h/3h + 150–360h/6h = 49 + 36 = 85 步
STEPS_MAIN = list(range(0, 145, 3)) + list(range(150, 361, 6))

# 辅助批次（06z/18z）：0–90h/3h = 31 步（非官方所称 1h/91 步）
STEPS_AUX = list(range(0, 91, 3))

STEP_PRESETS = {
    "short":  list(range(0, 73, 3)),      # 0~72h  / 3h, 25 步
    "medium": list(range(0, 121, 3)),     # 0~120h / 3h, 41 步
    "full":   STEPS_MAIN,                 # 0~360h / 85 步
}


def steps_for_run(run_hour: int) -> list:
    """按起报时次返回完整步长列表：00z/12z → 85 步，06z/18z → 31 步。"""
    return STEPS_MAIN if run_hour in (0, 12) else STEPS_AUX


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 要素预设（业务化脚本 --params 的候选取值）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RECOMMENDED_PARAMS = {
    "minimal": [
        "2t", "2d", "10u", "10v", "tp", "msl",
    ],
    "standard": [
        "2t", "2d", "10u", "10v", "tp", "msl", "sp", "tcc",
        "10fg", "mucape", "ssrd", "strd",
    ],
    "full_met": [
        "2t", "2d", "mn2t3", "mx2t3", "skt",
        "10u", "10v", "10fg", "100u", "100v",
        "msl", "sp", "tp", "cp", "sf", "ptype",
        "tcc", "lcc", "mcc", "hcc", "mucape",
        "ssrd", "strd", "ssr", "str", "ttr",
        "tcwv", "tclw", "tciw",
    ],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 代理自动检测（macOS 系统代理）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def detect_system_proxy() -> Optional[str]:
    """检测 macOS 系统代理设置，返回 http://host:port 或 None。"""
    if sys.platform != "darwin":
        return None
    try:
        import subprocess
        out = subprocess.check_output(["scutil", "--proxy"], timeout=5).decode()
        proxy = {}
        for line in out.strip().split("\n"):
            line = line.strip().rstrip(";")
            if ":" in line:
                k, v = line.split(":", 1)
                proxy[k.strip()] = v.strip()
        if proxy.get("HTTPSEnable") == "1" and proxy.get("HTTPSProxy"):
            return f"http://{proxy['HTTPSProxy']}:{proxy.get('HTTPSPort', '443')}"
        if proxy.get("HTTPEnable") == "1" and proxy.get("HTTPProxy"):
            return f"http://{proxy['HTTPProxy']}:{proxy.get('HTTPPort', '80')}"
    except Exception:
        pass
    return None


def setup_proxy(explicit: Optional[str] = None) -> Optional[str]:
    """设置 HTTP_PROXY / HTTPS_PROXY 环境变量，返回生效的代理地址或 None。"""
    proxy = explicit or detect_system_proxy()
    if proxy:
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("HTTPS_PROXY", proxy)
        return proxy
    return None