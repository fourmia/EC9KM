# samples — 本地回归基准（不进版本库）

本目录的 GRIB2 文件被 `.gitignore` 排除（`*.grib2`），仅存在于本地，用于回归验证与格式比对。

| 文件 | 来源 | 内容 | 大小 |
|------|------|------|------|
| `raw_sfc_20261022_00z_21vars.grib2` | ECMWF GCS `ecmwf-open-data` | 2026-10-22 00z 起报，地面层，21 个核心变量，0–72h/3h | 47 MB |

用途：

```bash
# 校验 GRIB2 变量清单与 paramId（无需联网）
python3 - <<'PY'
import eccodes
from collections import OrderedDict
params = OrderedDict()
with open("samples/raw_sfc_20261022_00z_21vars.grib2", "rb") as f:
    while (gid := eccodes.codes_grib_new_from_file(f)) is not None:
        sn = eccodes.codes_get(gid, "shortName")
        params.setdefault(sn, eccodes.codes_get(gid, "paramId"))
        eccodes.codes_release(gid)
for k, v in sorted(params.items()):
    print(f"{k:10s} {v}")
print(f"共 {len(params)} 个参数")   # 期望 21
PY
```

> 需要 `eccodes`（系统库）与 `eccodes` Python 绑定。预期输出 21 个参数，与
> [`docs/ecmwf_parameters.md`](../docs/ecmwf_parameters.md) 的核心变量表一致。