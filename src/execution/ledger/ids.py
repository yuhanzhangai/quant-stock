"""Ledger ID 生成:ULID(时间有序、可排序)+ 各表前缀约定(spec §3)。

不引第三方依赖:ULID = 48-bit 毫秒时间戳 + 80-bit 随机,Crockford base32 编码 26 字符。
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """生成一个 26 字符 ULID(同毫秒内不保证单调,排序锚是表内 seq/event_ts)。"""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (ts_ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def new_order_id() -> str:
    return f"ord_{ulid()}"


def new_fill_id() -> str:
    return f"fil_{ulid()}"


def new_pdt_entry_id() -> str:
    return f"pdt_{ulid()}"


def new_run_id() -> str:
    return f"run_{ulid()}"


def signal_id_for(tweet_id: str, ticker: str) -> str:
    """spec §3:signal_id = 'sig_' || tweet_id || '_' || ticker(幂等防重,必须含 ticker)。"""
    return f"sig_{tweet_id}_{ticker}"
