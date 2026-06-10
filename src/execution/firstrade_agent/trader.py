"""C6 写层:Firstrade 模拟盘下单。**本模块没有任何真金代码路径。**

安全设计(层层独立兜底,任何一层失败都拒单):
1. PAPER_ONLY 硬钉 + kill-switch:进函数先查;每个页面原语内部还会再查。
2. 模拟盘环境核验:页面上找不到 paper_account_marker → 触发 kill-switch 拒单
   (疑似页面异常/走错环境,按"出错先停"处理)。
3. 选择器核验:任何未经实盘核验的选择器直接拒跑(UnverifiedSelectorError 上抛)。
4. dry_run 默认 True:填表+预览但**绝不点最终提交**;实跑需调用方显式 dry_run=False。
5. 任何异常:先 engage kill-switch 再返回,不重试、不硬冲。
6. 每一步进 append-only 审计日志,operator 可逐单复盘。
"""

from __future__ import annotations

from src.execution.firstrade_agent.models import OrderIntent, OrderResult, OrderStatus, OrderType, Side
from src.execution.firstrade_agent.selectors import UnverifiedSelectorError
from src.execution.firstrade_agent.session import FirstradeSession
from src.execution.safety import ExecutionHalted


def place_paper_order(
    session: FirstradeSession,
    intent: OrderIntent,
    *,
    dry_run: bool = True,
) -> OrderResult:
    """在 Firstrade 模拟盘下一单(或 dry_run 演练)。返回结果,异常时已先停。"""
    # 第 1 层:安全闸
    try:
        session.kill.check()
    except ExecutionHalted as e:
        session.audit.record("order_halted", symbol=intent.symbol, reason=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=str(e))

    # 第 2 层:必须确认身处模拟盘环境,否则先停(核验本身出异常也先停)
    try:
        paper_confirmed = session.is_visible("paper_account_marker")
    except ExecutionHalted as e:
        session.audit.record("order_halted", symbol=intent.symbol, reason=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=str(e))
    except UnverifiedSelectorError:
        raise  # 选择器未核验是配置问题,如实上抛,逼着先走核验流程
    except Exception as e:
        session.kill.engage(f"模拟盘环境核验异常,先停: {e}")
        session.audit.record("order_error", symbol=intent.symbol, stage="paper_check", error=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=f"环境核验异常已停: {e}")

    if not paper_confirmed:
        session.kill.engage("下单前未见模拟盘环境标志(paper_account_marker),疑似页面异常,先停")
        detail = "无法确认模拟盘环境,已触发 kill-switch 拒单"
        session.audit.record("order_halted", symbol=intent.symbol, reason=detail)
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=detail)

    session.audit.record("order_intent", dry_run=dry_run, **intent.model_dump(mode="json"))

    # 填表 + 预览(全程真人节奏;任何异常先停)
    try:
        session.click("order_side_buy" if intent.side is Side.BUY else "order_side_sell")
        session.type_human("order_symbol_input", intent.symbol)
        session.type_human("order_qty_input", str(intent.qty))
        if intent.order_type is OrderType.LIMIT:
            session.click("order_type_limit")
            session.type_human("order_limit_price_input", str(intent.limit_price))
        else:
            session.click("order_type_market")
        session.click("order_preview_button")
    except ExecutionHalted as e:  # 中途被(别人)一键停:不再 engage,直接停
        session.audit.record("order_halted", symbol=intent.symbol, reason=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=str(e))
    except Exception as e:
        session.kill.engage(f"下单填表阶段异常,先停: {e}")
        session.audit.record("order_error", symbol=intent.symbol, stage="form", error=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=f"填表异常已停: {e}")

    # 重大动作前的"想一想"
    session.pacer.before_commit()

    if dry_run:
        session.audit.record("order_dry_run", symbol=intent.symbol)
        return OrderResult(
            intent=intent,
            status=OrderStatus.DRY_RUN,
            detail="表单已填并预览,dry_run 未点最终提交",
        )

    try:
        session.click("order_submit_button")
        confirmation = session.read_text("order_confirmation_text")
    except ExecutionHalted as e:
        session.audit.record("order_halted", symbol=intent.symbol, reason=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=str(e))
    except Exception as e:
        session.kill.engage(f"提交阶段异常,先停: {e}")
        session.audit.record("order_error", symbol=intent.symbol, stage="submit", error=str(e))
        return OrderResult(intent=intent, status=OrderStatus.HALTED, detail=f"提交阶段异常已停: {e}")

    session.audit.record("order_submitted", symbol=intent.symbol, confirmation=confirmation)
    return OrderResult(intent=intent, status=OrderStatus.SUBMITTED, detail=confirmation)
