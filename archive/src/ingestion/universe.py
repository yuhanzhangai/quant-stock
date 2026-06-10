"""标的池（Universe）更新器。"""

from loguru import logger

from src.exchange.ccxt_client import CCXTClient
from src.storage.state_tracker import StateTracker


class UniverseUpdater:
    """标的池更新器。

    从 OKX 获取现货 USDT 交易对，按 24h 成交额排名，
    保存 Top N 到 SQLite。
    """

    def __init__(
        self,
        ccxt_client: CCXTClient,
        state_tracker: StateTracker,
        top_n: int = 100,
    ) -> None:
        self._client = ccxt_client
        self._state = state_tracker
        self._top_n = top_n

    async def update(self) -> list[str]:
        """更新标的池。

        Returns:
            Top N 交易对列表（OKX 格式）
        """
        logger.info(f"开始更新标的池 (Top {self._top_n})")

        # 获取全部 Ticker
        tickers = await self._client.fetch_tickers()

        # 筛选 USDT 现货对，按成交额排序
        usdt_tickers = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            if ":" in symbol:  # 排除合约
                continue
            quote_vol = ticker.get("quoteVolume") or 0
            usdt_tickers.append((symbol, float(quote_vol)))

        usdt_tickers.sort(key=lambda x: x[1], reverse=True)
        top_symbols = usdt_tickers[: self._top_n]

        # 保存到 SQLite
        for rank, (symbol, vol) in enumerate(top_symbols, 1):
            okx_symbol = symbol.replace("/", "-")  # BTC/USDT -> BTC-USDT
            self._state.update_universe(
                symbol=okx_symbol,
                market_type="spot",
                quote_currency="USDT",
                volume_24h=vol,
                rank=rank,
            )

        result = [s.replace("/", "-") for s, _ in top_symbols]
        logger.info(f"标的池更新完成 | Top 5: {result[:5]} | 共 {len(result)} 个")
        return result
