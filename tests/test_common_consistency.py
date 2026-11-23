#!/usr/bin/env python3
"""
共享定义一致性检查（离线，无需网络）
=====================================
验证 `src/ecmwf_common.py` 与两个下载脚本之间的定义没有漂移。

用法:
    python3 tests/test_common_consistency.py
退出码 0 = 全部通过，1 = 有失败项。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import ecmwf_common as common  # noqa: E402

FAILED = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def main() -> int:
    # ── 地面层参数 ────────────────────────────────────────
    sfc_all = common.PARAMS_SFC_ALL
    check("SFC 参数共 45 个", len(sfc_all) == 45, f"{len(sfc_all)}")
    check("SFC 参数无重复", len(set(sfc_all)) == len(sfc_all))
    check(
        "核心 16 + 辐射 5 + 扩展 24 = 45",
        len(common.PARAMS_SFC_MET) == 16
        and len(common.PARAMS_SFC_RAD) == 5
        and len(common.PARAMS_SFC_EXTRA) == 24,
        f"{len(common.PARAMS_SFC_MET)}+{len(common.PARAMS_SFC_RAD)}+{len(common.PARAMS_SFC_EXTRA)}",
    )

    # ── 压力层 ───────────────────────────────────────────
    check("PL 变量 10 个", len(common.PARAMS_PL) == 10, f"{len(common.PARAMS_PL)}")
    check("PL 层次 14 层", len(common.LEVELS_PL) == 14, f"{len(common.LEVELS_PL)}")
    check(
        "PL 层次为单位 hPa 整数值且降序",
        common.LEVELS_PL == sorted(common.LEVELS_PL, reverse=True)
        and all(isinstance(v, int) for v in common.LEVELS_PL),
    )

    # ── 步长 ─────────────────────────────────────────────
    main_steps = common.STEPS_MAIN
    aux_steps = common.STEPS_AUX
    check("主批次 85 步", len(main_steps) == 85, f"{len(main_steps)}")
    check("主批次 0–360h", (main_steps[0], main_steps[-1]) == (0, 360))
    check(
        "主批次 0–144h 每 3h",
        main_steps[:49] == list(range(0, 145, 3)),
    )
    check(
        "主批次 150–360h 每 6h",
        main_steps[49:] == list(range(150, 361, 6)),
    )
    check("辅助批次 31 步", len(aux_steps) == 31, f"{len(aux_steps)}")
    check("辅助批次 0–90h 每 3h", aux_steps == list(range(0, 91, 3)))
    check(
        "steps_for_run: 00z/12z → 主批次",
        common.steps_for_run(0) is main_steps and common.steps_for_run(12) is main_steps,
    )
    check(
        "steps_for_run: 06z/18z → 辅助批次",
        common.steps_for_run(6) is aux_steps and common.steps_for_run(18) is aux_steps,
    )
    check(
        "--steps full 与主批次一致",
        common.STEP_PRESETS["full"] is main_steps,
    )
    check(
        "short/medium 为 3h 步长的前缀",
        common.STEP_PRESETS["short"] == list(range(0, 73, 3))
        and common.STEP_PRESETS["medium"] == list(range(0, 121, 3)),
    )

    # ── 预设要素是否都在可用清单内 ─────────────────────────
    for name, params in common.RECOMMENDED_PARAMS.items():
        unknown = sorted(set(params) - set(sfc_all))
        check(f"预设 {name} 的要素均可用", not unknown, f"未知: {unknown}" if unknown else "")

    # ── 脚本不再私自定义步长/参数 ──────────────────────────
    dl = (SRC / "download_ecmwf.py").read_text()
    cr = (SRC / "cron_fetch_ecmwf.py").read_text()

    check(
        "download_ecmwf.py 从共享模块导入参数与步长",
        "from ecmwf_common import" in dl
        and all(k in dl for k in ("PARAMS_SFC_MET", "STEP_PRESETS", "PARAMS_PL", "LEVELS_PL")),
    )
    check(
        "download_ecmwf.py 无本地 detect_system_proxy 定义",
        "def detect_system_proxy" not in dl,
    )
    check(
        "download_ecmwf.py 的 _SFC_SHORT_NAMES 由 PARAMS_SFC_ALL 派生",
        "_SFC_SHORT_NAMES = list(PARAMS_SFC_ALL)" in dl,
    )
    check(
        "cron_fetch_ecmwf.py 无硬编码步长 range",
        not re.search(r"range\(0,\s*14[45]", cr) and "steps_for_run(" in cr,
    )
    check(
        "cron_fetch_ecmwf.py 无本地 RECOMMENDED_PARAMS 定义",
        "RECOMMENDED_PARAMS = {" not in cr,
    )
    check(
        "cron_fetch_ecmwf.py 无本地 detect_system_proxy 定义",
        "def detect_system_proxy" not in cr,
    )
    check(
        "cron_fetch_era5.py 无本地 detect_system_proxy 定义",
        "def detect_system_proxy" not in (SRC / "cron_fetch_era5.py").read_text(),
    )

    check(
        "两个下载脚本都经由 retry_call 重试",
        "retry_call(" in dl and "retry_call(" in cr,
    )
    check(
        "cron_fetch_ecmwf.py 无本地重试循环",
        "for attempt in range(1, retry + 1)" not in cr,
    )
    check(
        "download_ecmwf.py 暴露 --retry / --retry-delay",
        '"--retry"' in dl and '"--retry-delay"' in dl,
    )

    # ── 文档中不得残留 240h / 1h 步长表述 ──────────────────
    for md in sorted((ROOT / "docs").glob("*.md")):
        text = md.read_text()
        if md.name in {"TEST_FOUR_CYCLES_FINAL.md", "VERIFICATION_360H.md", "DATA_CHANNEL_SPEC.md"}:
            continue  # 这三篇是对勘误过程的记录，允许出现历史值
        check(
            f"{md.name} 未残留 1h 步长表述",
            "0–90h（1h步）" not in text,
        )

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败: {', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())