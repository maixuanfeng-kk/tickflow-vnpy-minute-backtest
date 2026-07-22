"""早盘放量组合的原生分钟策略元数据。"""

META = {
    "id": "opening_volume_portfolio",
    "name": "早盘放量组合",
    "description": "在 TickFlow 自选股中扫描满足任一启用入场分支的早盘股票。各分支独立判断量能。",
    "tags": ["早盘", "放量", "自选股"],
    "asset_types": ["stock"],
    "timeframes": ["1m"],
    "scanner_backend": "opening_volume",
    "params": [
        {"id": "scan_start_time", "label": "扫描开始时间", "type": "time", "default": "09:30"},
        {"id": "scan_end_time", "label": "扫描结束时间", "type": "time", "default": "09:59"},
        {"id": "enable_branch_a", "label": "A 分支开关", "type": "bool", "default": True},
        {"id": "branch_a_volume_multiple", "label": "A 量能倍数", "type": "float", "default": 1.5, "min": 0.1, "step": 0.1},
        {"id": "branch_a_previous_candle", "label": "A 前日 K 线", "type": "select", "default": "阴线", "options": ["阴线", "阳线", "不限"]},
        {"id": "enable_branch_b", "label": "B 分支开关", "type": "bool", "default": True},
        {"id": "branch_b_volume_multiple", "label": "B 量能倍数", "type": "float", "default": 1.5, "min": 0.1, "step": 0.1},
        {"id": "branch_b_today_return_min", "label": "B 当日涨幅下限", "type": "percent", "default": 0.03, "min": -1, "max": 1, "step": 0.001},
        {"id": "branch_b_today_return_max", "label": "B 当日涨幅上限", "type": "percent", "default": 0.05, "min": -1, "max": 1, "step": 0.001},
        {"id": "branch_b_previous_return_max", "label": "B 前日涨幅上限", "type": "percent", "default": 0.05, "min": -1, "max": 1, "step": 0.001},
        {"id": "enable_branch_c", "label": "C 分支开关", "type": "bool", "default": True},
        {"id": "branch_c_volume_multiple", "label": "C 量能倍数", "type": "float", "default": 1.5, "min": 0.1, "step": 0.1},
        {"id": "branch_c_previous_candle", "label": "C 前日 K 线", "type": "select", "default": "阳线", "options": ["阴线", "阳线", "不限"]},
        {"id": "branch_c_previous_return_max", "label": "C 前日涨幅上限", "type": "percent", "default": 0.05, "min": -1, "max": 1, "step": 0.001},
        {"id": "stop_loss_pct", "label": "止损比例", "type": "percent", "default": 0.02, "min": 0},
        {"id": "ma_exit_period", "label": "均线出场周期", "type": "enum", "default": 5, "options": [5, 10, 20, 30, 60]},
    ],
}

EXECUTION_BACKEND = "minute_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = None
ALERTS = []
