"""个股分析 API — 关键价位 + AI 四维分析 + FinSight 深度研报 + 报告持久化。

路由前缀: /api/stock-analysis

端点:
  GET  /levels?symbol=         11 类关键价位(图表 markLine 数据源)
  POST /analyze                AI 流式四维分析(NDJSON)
  GET  /reports                历史报告列表
  POST /reports                保存一条报告
  DELETE /reports/{report_id}  删除一条报告
  GET/POST /deep-reports/*     FinSight 深度研究报告编排
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Literal

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.indicators.levels import compute_levels, summarize_levels
from app.services import deep_report_tasks, deep_stock_reports, stock_reports
from app.services.stock_analyzer import analyze_stock_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-analysis", tags=["stock-analysis"])


def _to_float_list(series: pl.Series) -> list:
    """polars Series → JSON 安全的 float 列表(null/NaN → None)。"""
    out: list = []
    for v in series.to_list():
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
            out.append(round(f, 2) if math.isfinite(f) else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _build_series(df: pl.DataFrame) -> dict:
    """提取带状指标(布林带 / Keltner通道 / ATR止损)的每日时间序列。

    这些指标的本质是"每日一条线",随 MA/ATR/σ 漂移,画成曲线才能体现通道形态。
    其余固定价位(枢轴/前高前低等)不在此,仍用水平 markLine。

    返回结构(每个 value 都是按日期对齐的数组):
      {
        "boll":      {"upper": [...], "lower": [...]},
        "keltner_s": {"upper": [...], "lower": [...]},   # 短期 MA20±2ATR
        "keltner_m": {"upper": [...], "lower": [...]},   # 中期 MA60±2.5ATR
        "keltner_l": {"upper": [...], "lower": [...]},   # 长期 MA120±3ATR
        "atr":       {"stop_loss": [...], "take_profit": [...]},  # close∓2ATR
      }
    """
    if df.is_empty() or "close" not in df.columns:
        return {}

    out: dict[str, dict] = {}
    close = df["close"]
    has_atr = "atr_14" in df.columns

    # 布林带(上/下/中轨;中轨 = MA20,数据层已预计算)
    if "boll_upper" in df.columns and "boll_lower" in df.columns:
        out["boll"] = {
            "upper": _to_float_list(df["boll_upper"]),
            "lower": _to_float_list(df["boll_lower"]),
            "mid": _to_float_list(df["ma20"]) if "ma20" in df.columns else None,
        }

    # Keltner 通道三档(需要 ATR)
    if has_atr:
        atr = df["atr_14"]
        # MA120 现场算(不在预计算列中)
        ma120 = df.select(pl.col("close").rolling_mean(120))["close"] if df.height >= 120 else None

        def _channel(ma: pl.Series, n: float) -> dict:
            return {
                "upper": _to_float_list(ma + n * atr),
                "lower": _to_float_list(ma - n * atr),
            }

        if "ma20" in df.columns:
            out["keltner_s"] = _channel(df["ma20"], 2.0)
        if "ma60" in df.columns:
            out["keltner_m"] = _channel(df["ma60"], 2.5)
        if ma120 is not None:
            out["keltner_l"] = _channel(ma120, 3.0)

        # ATR 止损/止盈: close ± 2×ATR(跟随行情漂移的动态止损线)
        out["atr"] = {
            "stop_loss": _to_float_list(close - 2 * atr),
            "take_profit": _to_float_list(close + 2 * atr),
        }

    return out


@router.get("/levels")
def get_levels(
    request: Request,
    symbol: str = Query(..., description="标的代码,如 000001.SZ"),
    days: int = Query(120, ge=30, le=500, description="计算样本天数"),
):
    """计算 11 类关键价位(成交密集区压力支撑 / 枢轴点 / 前高前低 /
    布林带 / Keltner短中长 / ATR止损 / 缺口 / 斐波那契 / 整数关口)。

    返回 {levels: {sr, pivot, extreme, boll, keltner_s, keltner_m, keltner_l,
    atr_stop, gap, fib, round}, close, summary, dates, series}。
    前端按 levels 的 key 渲染开关按钮,逐组显隐 markLine / 曲线。
    """
    if not symbol:
        raise HTTPException(400, "symbol 不能为空")

    repo = request.app.state.repo
    end = date.today()
    start = end - timedelta(days=days * 2)
    # 按资产类型分流: ETF/指数走独立 enriched 存储, 股票保持原路径
    df = repo.get_daily_asset(repo.resolve_asset_type(symbol), symbol, start, end)
    if df.is_empty():
        return {"levels": {"sr": [], "pivot": [], "extreme": [],
                           "boll": [], "keltner_s": [], "keltner_m": [], "keltner_l": [],
                           "atr_stop": [], "gap": [], "fib": [], "round": []},
                "close": None, "summary": "无数据", "symbol": symbol,
                "dates": [], "series": {}}

    levels = compute_levels(df)
    close = float(df.tail(1)["close"][0]) if "close" in df.columns else None
    # 日期 + 带状曲线序列(供前端画 Keltner/ATR/布林带曲线)
    dates = df["date"].to_list()
    series = _build_series(df)
    return {
        "levels": levels,
        "close": close,
        "summary": summarize_levels(levels, close),
        "symbol": symbol,
        "dates": [str(d) for d in dates],
        "series": series,
    }


class AnalyzeRequest(BaseModel):
    """AI 个股分析请求。"""
    symbol: str
    focus: str = ""  # 可选:用户追加的分析关注点


@router.post("/analyze")
async def analyze_stock(request: Request, req: AnalyzeRequest):
    """AI 个股四维分析 — NDJSON 流式返回。

    组合 K 线(技术指标)+ 财务表 + 关键价位 → 客观技术分析提示词 →
    流式调用 LLM → 逐 chunk 以 NDJSON 推给前端(每行一个 JSON)。
    """
    if not req.symbol:
        raise HTTPException(400, "symbol 不能为空")

    repo = request.app.state.repo
    data_dir = repo.store.data_dir

    async def stream_gen():
        async for chunk in analyze_stock_stream(repo, data_dir, req.symbol, req.focus):
            yield chunk + "\n"

    return StreamingResponse(
        stream_gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================
# 报告 CRUD(历史报告持久化)
# ================================================================

class SaveReportRequest(BaseModel):
    """保存一条 AI 个股分析报告。"""
    symbol: str
    name: str = ""
    focus: str = ""
    content: str
    summary: str = ""
    close: float | None = None
    levels: dict | None = None


@router.get("/reports")
def list_reports(request: Request):
    """获取全部历史报告(按时间降序,后端已裁剪到上限)。"""
    return {"reports": stock_reports.list_reports()}


@router.post("/reports")
def save_report(request: Request, req: SaveReportRequest):
    """保存一条报告。"""
    report = stock_reports.save_report({
        "symbol": req.symbol,
        "name": req.name,
        "focus": req.focus,
        "content": req.content,
        "summary": req.summary,
        "close": req.close,
        "levels": req.levels,
    })
    return {"ok": True, "report": report}


@router.delete("/reports/{report_id}")
def delete_report(request: Request, report_id: str):
    """删除一条报告。"""
    ok = stock_reports.delete_report(report_id)
    return {"ok": ok}


class CreateDeepReportRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    name: str = Field(default="", max_length=100)
    collect_task_ids: list[str] = Field(min_length=1, max_length=50)
    analysis_task_ids: list[str] = Field(min_length=1, max_length=50)


class DeepReportTaskIn(BaseModel):
    kind: Literal["collect", "analysis"]
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=8000)
    enabled: bool = True


class DeepReportTaskOrderIn(BaseModel):
    kind: Literal["collect", "analysis"]
    task_ids: list[str] = Field(max_length=100)


@router.get("/deep-reports/health")
def get_deep_report_health() -> dict:
    return deep_stock_reports.integration_health()


@router.get("/deep-reports/defaults")
def get_deep_report_default_tasks() -> dict:
    return deep_stock_reports.default_task_templates()


@router.get("/deep-reports/task-catalog")
def get_deep_report_task_catalog() -> dict:
    return deep_report_tasks.get_catalog(active_only=True)


@router.get("/deep-reports/task-catalog/admin")
def get_deep_report_task_catalog_admin() -> dict:
    return deep_report_tasks.get_catalog(active_only=False)


@router.post("/deep-reports/task-catalog/admin")
def create_deep_report_task(req: DeepReportTaskIn) -> dict:
    try:
        task = deep_report_tasks.create_task(
            kind=req.kind,
            title=req.title,
            prompt=req.prompt,
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.put("/deep-reports/task-catalog/admin/{task_id}")
def update_deep_report_task(task_id: str, req: DeepReportTaskIn) -> dict:
    try:
        task = deep_report_tasks.update_task(
            task_id,
            kind=req.kind,
            title=req.title,
            prompt=req.prompt,
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"task": task}


@router.delete("/deep-reports/task-catalog/admin/{task_id}")
def delete_deep_report_task(task_id: str) -> dict:
    try:
        deep_report_tasks.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/deep-reports/task-catalog/admin/reorder")
def reorder_deep_report_tasks(req: DeepReportTaskOrderIn) -> dict:
    try:
        return deep_report_tasks.reorder_tasks(req.kind, req.task_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/deep-reports/runs")
def create_deep_report_run(req: CreateDeepReportRequest) -> dict:
    try:
        run = deep_stock_reports.create_run(
            symbol=req.symbol,
            name=req.name,
            collect_task_ids=req.collect_task_ids,
            analysis_task_ids=req.analysis_task_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except deep_stock_reports.ConcurrentRunLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except deep_stock_reports.IntegrationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except deep_stock_reports.RunnerStartError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"run": run}


@router.get("/deep-reports/runs")
def list_deep_report_runs(symbol: str | None = Query(default=None)) -> dict:
    try:
        return {"runs": deep_stock_reports.list_runs(symbol)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/deep-reports/runs/{run_id}")
def get_deep_report_run(run_id: str) -> dict:
    try:
        return {"run": deep_stock_reports.get_run(run_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deep-reports/runs/{run_id}/cancel")
def cancel_deep_report_run(run_id: str) -> dict:
    try:
        return {"run": deep_stock_reports.cancel_run(run_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/deep-reports/runs/{run_id}/download/{kind}")
def download_deep_report_artifact(run_id: str, kind: str):
    try:
        path, media_type, filename = deep_stock_reports.resolve_artifact(run_id, kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=path, media_type=media_type, filename=filename)


@router.delete("/deep-reports/runs/{run_id}")
def delete_deep_report_run(run_id: str) -> dict:
    try:
        deep_stock_reports.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except deep_stock_reports.ActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
