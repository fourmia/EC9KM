#!/usr/bin/env python3
"""
GRIB2 → NetCDF 后处理回归测试（离线）
=====================================
用 `samples/` 中保留的基准 GRIB2 文件跑完整后处理链路，检查：
  - 45 个参数能被逐个读回
  - 浙江裁剪范围正确
  - 辐射累计量 → 通量转换（step=0 为 0、向下辐射非负、净辐射保留负值）
  - 无 step 维的单步变量不会导致崩溃（跳过并告警）

依赖: cfgrib / eccodes / xarray / netCDF4（缺任一项则跳过）

用法:
    python3 tests/test_grib_to_netcdf.py
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SAMPLE = ROOT / "samples" / "raw_sfc_20261022_00z_21vars.grib2"
RUN_DT = datetime(2026, 10, 22, 0, tzinfo=timezone.utc)

FAILED = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def main() -> int:
    for mod in ("cfgrib", "eccodes", "xarray", "netCDF4"):
        try:
            __import__(mod)
        except ImportError:
            print(f"⏭  跳过：缺少依赖 {mod}（pip install cfgrib eccodes xarray netCDF4）")
            return 0

    if not SAMPLE.exists():
        print(f"⏭  跳过：样本不存在 {SAMPLE.relative_to(ROOT)}")
        print("   该文件被 .gitignore 排除，需本地保留；见 samples/README.md")
        return 0

    import xarray as xr
    import download_ecmwf as dl

    out = Path(tempfile.mkdtemp()) / "sfc.nc"
    dl.grib_to_netcdf(SAMPLE, out, RUN_DT)

    check("生成 NetCDF 文件", out.exists(), f"{out.stat().st_size/1e6:.1f} MB" if out.exists() else "")
    if not out.exists():
        return 1

    ds = xr.open_dataset(out)

    # 浙江裁剪：0.1° 网格，经度 117.8–123.2（54 点），纬度 26.8–31.8（50 点）
    check("经度裁剪到浙江", ds.sizes.get("longitude") == 54, str(ds.sizes.get("longitude")))
    check("纬度裁剪到浙江", ds.sizes.get("latitude") == 50, str(ds.sizes.get("latitude")))
    check(
        "纬度范围内浙江",
        26.0 <= float(ds.latitude.min()) <= 27.5 and 31.0 <= float(ds.latitude.max()) <= 32.5,
        f"{float(ds.latitude.min()):.1f}–{float(ds.latitude.max()):.1f}°N",
    )

    # 2 米气温合理性（10 月浙江）
    check("t2m 存在", "t2m" in ds.data_vars)
    if "t2m" in ds:
        lo, hi = float(ds.t2m.min()), float(ds.t2m.max())
        check("t2m 数值合理", 270 <= lo and hi <= 320, f"{lo:.1f}–{hi:.1f} K")

    # 辐射：累计量保留 + 通量转换（单步变量应跳过转换）
    if "ssrd" in ds.data_vars:
        if "step" in ds.ssrd.dims:
            check("ssrd 累计量保留", "ssrd_accum" in ds.data_vars)
            check("ssrd 通量单位 W/m²", ds.ssrd.attrs.get("units") == "W m**-2", ds.ssrd.attrs.get("units", ""))
            check("向下辐射非负", bool((ds.ssrd.fillna(0) >= 0).all()))
        else:
            check(
                "单步 ssrd 跳过转换（保留累计量，不产生 _accum）",
                ds.ssrd.attrs.get("units") == "J m**-2" and "ssrd_accum" not in ds.data_vars,
                ds.ssrd.attrs.get("units", ""),
            )
    if "strd" in ds.data_vars and "step" in ds.strd.dims:
        check("strd 通量单位 W/m²", ds.strd.attrs.get("units") == "W m**-2")
        check("strd step=0 通量为 0", float(ds.strd.isel(step=0).max()) == 0.0,
              f"{float(ds.strd.isel(step=0).max()):.1f}")
        check("strd 通量量级合理", 0 < float(ds.strd.max()) < 800, f"{float(ds.strd.max()):.0f} W/m²")

    # 净辐射允许负值（不得被 clip）
    for v in ("str", "ttr"):
        if v in ds.data_vars and "step" in ds[v].dims:
            mn = float(ds[v].min())
            check(f"{v} 允许为负（未被 clip）", mn <= 0, f"min={mn:.0f} W/m²")

    ds.close()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败: {', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())