"""Telegram 信号通知：交易信号自动推送到手机。

配置：在 .env 中设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID
获取方式：
  1. 找 @BotFather 创建 bot，获取 token
  2. 给 bot 发消息后，访问 https://api.telegram.org/bot<TOKEN>/getUpdates 获取 chat_id
"""

import aiohttp
from loguru import logger

from config.settings import get_settings


async def send_telegram(message: str) -> bool:
    """发送 Telegram 消息。

    Args:
        message: 消息内容（支持 Markdown）

    Returns:
        是否发送成功
    """
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram 未配置，跳过通知")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.debug(f"Telegram 通知已发送")
                    return True
                else:
                    data = await resp.json()
                    logger.warning(f"Telegram 发送失败: {data}")
                    return False
    except Exception as e:
        logger.warning(f"Telegram 连接失败: {e}")
        return False


async def notify_signal(
    symbol: str,
    signal_type: str,
    price: float,
    stop_loss: float = 0,
    take_profit: float = 0,
    rsi: float = 0,
    trend: str = "",
) -> None:
    """发送交易信号通知。"""
    if signal_type == "ENTRY":
        emoji = "🟢"
        msg = (
            f"{emoji} *ENTRY SIGNAL*\n"
            f"```\n"
            f"Coin:  {symbol}\n"
            f"Price: ${price:,.2f}\n"
            f"SL:    ${stop_loss:,.2f}\n"
            f"TP:    ${take_profit:,.2f}\n"
            f"RSI:   {rsi:.0f}\n"
            f"Trend: {trend}\n"
            f"```"
        )
    elif signal_type == "EXIT":
        emoji = "🔴"
        msg = f"{emoji} *EXIT SIGNAL*\n`{symbol} @ ${price:,.2f}`"
    else:
        msg = f"ℹ️ {symbol}: {signal_type}"

    await send_telegram(msg)


async def notify_daily_summary(summary: str) -> None:
    """发送每日总结。"""
    msg = f"📊 *Daily Report*\n```\n{summary}\n```"
    await send_telegram(msg)
