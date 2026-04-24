"""Exit mode implementations for paired replay.

Each exit mode takes (price, entry_bar_idx, entry_price, params) and returns
(exit_bar_idx, exit_price, exit_reason).

All exit modes use the SAME entry — only exit logic differs.
"""

import pandas as pd


def current_exit(
    price: pd.Series,
    entry_idx: int,
    entry_price: float,
    trend_ma: int = 180,
    stop_pct: float = 2.0,
    take_profit_pct: float = 8.0,
    trail: bool = True,
    trail_pct: float = 2.0,
) -> tuple[int, float, str]:
    """Standard MinSwing v3 exit (production default).

    ETH: trailing stop. SOL/ARB: fixed TP.
    """
    ma = price.rolling(window=trend_ma).mean()

    if trail:
        peak = entry_price
        for i in range(entry_idx + 1, len(price)):
            p = float(price.iloc[i])
            if p > peak:
                peak = p
            pnl = (p - entry_price) / entry_price * 100
            dd = (peak - p) / peak * 100
            ma_val = ma.iloc[i] if pd.notna(ma.iloc[i]) else 0
            if pnl < -stop_pct or dd > trail_pct or p < ma_val:
                return (
                    i,
                    p,
                    "trailing_stop" if dd > trail_pct else ("stop_loss" if pnl < -stop_pct else "trend_reversal"),
                )
        return len(price) - 1, float(price.iloc[-1]), "end_of_data"
    else:
        for i in range(entry_idx + 1, len(price)):
            p = float(price.iloc[i])
            pnl = (p - entry_price) / entry_price * 100
            ma_val = ma.iloc[i] if pd.notna(ma.iloc[i]) else 0
            if pnl < -stop_pct or pnl > take_profit_pct or p < ma_val:
                return (
                    i,
                    p,
                    "stop_loss" if pnl < -stop_pct else ("take_profit" if pnl > take_profit_pct else "trend_reversal"),
                )
        return len(price) - 1, float(price.iloc[-1]), "end_of_data"


def fast_exit(
    price: pd.Series,
    entry_idx: int,
    entry_price: float,
    trend_ma: int = 180,
    stop_pct: float = 2.0,
    take_profit_pct: float = 8.0,
    fast_ma: int = 90,
    profit_thr: float = 0.3,
) -> tuple[int, float, str]:
    """FastExit: fast MA death cross early exit when profitable."""
    ma_slow = price.rolling(window=trend_ma).mean()
    sma_f = price.rolling(window=fast_ma).mean()
    sma_h = price.rolling(window=fast_ma // 2).mean()
    death = (sma_h < sma_f) & (sma_h.shift(1) >= sma_f.shift(1))

    for i in range(entry_idx + 1, len(price)):
        p = float(price.iloc[i])
        pnl = (p - entry_price) / entry_price * 100
        ma_val = ma_slow.iloc[i] if pd.notna(ma_slow.iloc[i]) else 0

        if pnl < -stop_pct or pnl > take_profit_pct or p < ma_val:
            return (
                i,
                p,
                "stop_loss" if pnl < -stop_pct else ("take_profit" if pnl > take_profit_pct else "trend_reversal"),
            )
        if death.iloc[i] and pnl > profit_thr:
            return i, p, "fast_exit_death_cross"

    return len(price) - 1, float(price.iloc[-1]), "end_of_data"


def trailing_exit(
    price: pd.Series,
    entry_idx: int,
    entry_price: float,
    trend_ma: int = 180,
    stop_pct: float = 2.0,
    trail_pct: float = 2.0,
) -> tuple[int, float, str]:
    """Pure trailing stop (no fixed TP)."""
    ma = price.rolling(window=trend_ma).mean()
    peak = entry_price

    for i in range(entry_idx + 1, len(price)):
        p = float(price.iloc[i])
        if p > peak:
            peak = p
        pnl = (p - entry_price) / entry_price * 100
        dd = (peak - p) / peak * 100
        ma_val = ma.iloc[i] if pd.notna(ma.iloc[i]) else 0

        if pnl < -stop_pct:
            return i, p, "stop_loss"
        if dd > trail_pct:
            return i, p, "trailing_stop"
        if p < ma_val:
            return i, p, "trend_reversal"

    return len(price) - 1, float(price.iloc[-1]), "end_of_data"


def hybrid_exit(
    price: pd.Series,
    entry_idx: int,
    entry_price: float,
    trend_ma: int = 180,
    stop_pct: float = 2.0,
    fast_ma: int = 90,
    profit_thr: float = 0.3,
    trail_pct: float = 2.0,
) -> tuple[int, float, str]:
    """Hybrid: SL priority → fast_exit → trailing (experimental)."""
    ma_slow = price.rolling(window=trend_ma).mean()
    sma_f = price.rolling(window=fast_ma).mean()
    sma_h = price.rolling(window=fast_ma // 2).mean()
    death = (sma_h < sma_f) & (sma_h.shift(1) >= sma_f.shift(1))
    peak = entry_price

    for i in range(entry_idx + 1, len(price)):
        p = float(price.iloc[i])
        if p > peak:
            peak = p
        pnl = (p - entry_price) / entry_price * 100
        dd = (peak - p) / peak * 100
        ma_val = ma_slow.iloc[i] if pd.notna(ma_slow.iloc[i]) else 0

        # Priority 1: stop loss
        if pnl < -stop_pct:
            return i, p, "stop_loss"
        # Priority 2: fast exit (death cross when profitable)
        if death.iloc[i] and pnl > profit_thr:
            return i, p, "fast_exit_death_cross"
        # Priority 3: trailing stop
        if dd > trail_pct:
            return i, p, "trailing_stop"
        # Priority 4: trend reversal
        if p < ma_val:
            return i, p, "trend_reversal"

    return len(price) - 1, float(price.iloc[-1]), "end_of_data"


EXIT_MODE_FUNCS = {
    "current_exit": current_exit,
    "fast_exit": fast_exit,
    "trailing_exit": trailing_exit,
    "hybrid_exit": hybrid_exit,
}
