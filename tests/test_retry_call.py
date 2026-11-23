#!/usr/bin/env python3
"""
retry_call 行为测试（离线，不联网）
===================================
验证共享重试原语：成功即返回、失败重试、清理回调、耗尽后抛异常、
重试次数为 1 时等价于不重试。

用法:
    python3 tests/test_retry_call.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ecmwf_common import retry_call  # noqa: E402

FAILED = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"{'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def main() -> int:
    slept = []

    # ① 首次成功：只调用 1 次，不 sleep
    calls = []

    def ok():
        calls.append(1)
        return "ok"

    r = retry_call(ok, retries=3, delay=5, sleep=slept.append)
    check("首次成功直接返回", r == "ok" and len(calls) == 1, f"调用 {len(calls)} 次")
    check("成功时不 sleep", slept == [], str(slept))

    # ② 前两次失败，第三次成功
    calls.clear(); slept.clear()

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("File size mismatch 999 of 1000")
        return "recovered"

    r = retry_call(flaky, retries=3, delay=7, sleep=slept.append)
    check("第三次成功返回", r == "recovered" and len(calls) == 3, f"调用 {len(calls)} 次")
    check("重试间隔生效", slept == [7, 7], str(slept))

    # ③ 清理回调在每次重试前执行
    calls.clear(); slept.clear(); cleaned = []

    def bad():
        calls.append(1)
        raise RuntimeError("boom")

    try:
        retry_call(bad, retries=3, delay=0, sleep=slept.append, cleanup=lambda: cleaned.append(1))
    except RuntimeError as e:
        check("耗尽后抛出最后一次异常", str(e) == "boom")
    check("调用次数等于 retries", len(calls) == 3, f"{len(calls)}")
    check("清理回调次数 = retries-1", len(cleaned) == 2, f"{len(cleaned)}")

    # ④ retries=1：只尝试一次
    calls.clear(); slept.clear()
    try:
        retry_call(bad, retries=1, delay=0, sleep=slept.append)
    except RuntimeError:
        pass
    check("retries=1 只尝试一次且不等待", len(calls) == 1 and slept == [])

    # ⑤ 异常类型与参数透传
    def with_args(a, b, key=None):
        return (a, b, key)

    r = retry_call(with_args, 1, 2, key="v", retries=2, sleep=slept.append)
    check("位置/关键字参数透传", r == (1, 2, "v"), str(r))

    # ⑥ cleanup 抛异常不影响重试
    calls.clear()
    n = {"i": 0}

    def once_then_ok():
        n["i"] += 1
        if n["i"] == 1:
            raise OSError("first fails")
        return "ok"

    def bad_cleanup():
        raise RuntimeError("cleanup failed")

    r = retry_call(once_then_ok, retries=2, delay=0, sleep=slept.append, cleanup=bad_cleanup)
    check("清理失败不阻断重试", r == "ok", str(r))

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败: {', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())