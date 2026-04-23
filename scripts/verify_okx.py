"""验证 OKX API 连通性和 API Key 有效性。"""

import asyncio
import sys
import time
from pathlib import Path

import aiohttp
from loguru import logger

# 让 import config 能找到项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_settings

OKX_BASE_URL = "https://www.okx.com"


async def check_public_time() -> bool:
    """检查 OKX 公开接口连通性（GET /api/v5/public/time）。"""
    url = f"{OKX_BASE_URL}/api/v5/public/time"
    logger.info(f"正在连接 OKX 公开接口: {url}")

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                data = await resp.json()
                logger.info(
                    f"响应状态码: {resp.status} | 耗时: {elapsed_ms:.0f}ms"
                )

                if resp.status == 200 and data.get("code") == "0":
                    server_ts = int(data["data"][0]["ts"])
                    local_ts = int(time.time() * 1000)
                    drift_ms = abs(local_ts - server_ts)
                    logger.info(f"服务器时间戳: {server_ts} | 本地时间戳: {local_ts} | 偏差: {drift_ms}ms")

                    if drift_ms > 30000:
                        logger.warning(f"时间偏差 > 30秒 ({drift_ms}ms)，签名可能失败，请检查 NTP")
                    else:
                        logger.success("时间同步正常")

                    return True
                else:
                    logger.error(f"接口返回异常: {data}")
                    return False

    except aiohttp.ClientError as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error(f"连接失败 ({elapsed_ms:.0f}ms): {e}")
        logger.info("提示: 如果在中国大陆，请确认 VPN/代理已开启")
        return False


async def check_api_key() -> bool:
    """检查 API Key 是否有效（需要鉴权的只读接口）。"""
    settings = get_settings()

    if not settings.okx.api_key:
        logger.warning("未配置 OKX_API_KEY，跳过鉴权测试")
        logger.info("请在 .env 文件中配置 OKX_API_KEY、OKX_API_SECRET、OKX_PASSPHRASE")
        return True  # 不算失败

    import base64
    import hashlib
    import hmac
    from datetime import datetime, timezone

    # 生成签名
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    method = "GET"
    request_path = "/api/v5/account/balance"
    message = timestamp + method + request_path
    signature = base64.b64encode(
        hmac.new(
            settings.okx.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()

    headers = {
        "OK-ACCESS-KEY": settings.okx.api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": settings.okx.passphrase,
    }

    if settings.okx.use_simulated:
        headers["x-simulated-trading"] = "1"

    url = f"{OKX_BASE_URL}{request_path}"
    logger.info(f"正在验证 API Key: {url}")

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                elapsed_ms = (time.monotonic() - start) * 1000
                data = await resp.json()
                logger.info(f"响应状态码: {resp.status} | 耗时: {elapsed_ms:.0f}ms")

                if resp.status == 200 and data.get("code") == "0":
                    logger.success("API Key 验证通过（只读权限正常）")
                    return True
                else:
                    error_msg = data.get("msg", "未知错误")
                    error_code = data.get("code", "未知")
                    logger.error(f"API Key 验证失败: [{error_code}] {error_msg}")
                    return False

    except aiohttp.ClientError as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error(f"请求失败 ({elapsed_ms:.0f}ms): {e}")
        return False


async def main() -> None:
    """运行所有连通性检查。"""
    logger.info("=" * 50)
    logger.info("OKX 连通性验证")
    logger.info("=" * 50)

    # 1. 公开接口
    public_ok = await check_public_time()
    if not public_ok:
        logger.error("公开接口不可达，终止验证")
        sys.exit(1)

    logger.info("")

    # 2. API Key（如果配置了）
    key_ok = await check_api_key()

    logger.info("")
    logger.info("=" * 50)
    if public_ok and key_ok:
        logger.success("所有检查通过！")
    else:
        logger.error("存在检查未通过，请查看上方日志")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
