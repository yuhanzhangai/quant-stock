# Strategies

## Folder Structure

```
strategies/
  base.py          # Base class for all strategies
  long/            # 40 long (buy) strategies
  short/           # 11 short (sell) strategies  
  meta/            # 6 meta strategies (ensemble, routing, selection)
  us_stock/        # US stock contract strategies (TSLA etc.)
```

## Long Strategies (40)
Core: MinSwing v3 (the proven winner)
- minswing_v3_final.py - Best 5m strategy, sharpe +2.4
- minute_swing.py - Original MinSwing
- trend_ma_filtered.py - 4h trend following
- ichimoku_momentum.py - Ichimoku + momentum combo
- extreme_reversal.py - Crash dip buying
- aggressive_momentum.py - Momentum chasing

## Short Strategies (11)
Core: ShortSwing (SOL 3/3 positive)
- short_swing.py - Base short strategy
- short_trend_follow.py - Trend following shorts
- short_rsi_overbought.py - RSI overbought reversal
- short_spike_fade.py - Spike fading
- short_vol_atr.py - Volatility based shorts

## Meta Strategies (6)
- dynamic_selector.py - Auto strategy switching (sharpe +1.051)
- robust_ensemble.py - Top 4 ROBUST strategies voting
- per_coin_router.py - Per-coin optimal params

## US Stock (1)
- tsla_news_event.py - Tesla news event strategy
