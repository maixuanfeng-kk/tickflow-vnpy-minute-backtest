"""早盘放量组合的原生分钟策略元数据。"""

META = {
    "id": "opening_volume_portfolio",
    "name": "早盘放量组合",
    "description": "在 TickFlow 自选股中扫描早盘放量且满足任一启用入场分支的股票。",
    "tags": ["早盘", "放量", "自选股"],
    "asset_types": ["stock"],
    "timeframes": ["1m"],
    "scanner_backend": "opening_volume",
    "params": [
        {"id": "scan_start_time", "label": "扫描开始时间", "type": "time", "default": "09:30"},
        {"id": "scan_end_time", "label": "扫描结束时间", "type": "time", "default": "09:59"},
        {"id": "volume_multiple", "label": "量能倍数", "type": "float", "default": 1.5, "min": 0.1},
        {"id": "enable_branch_a", "label": "启用 A 入场分支", "type": "bool", "default": True},
        {"id": "enable_branch_b", "label": "启用 B 入场分支", "type": "bool", "default": True},
        {"id": "enable_branch_c", "label": "启用 C 入场分支", "type": "bool", "default": True},
        {"id": "stop_loss_pct", "label": "止损比例", "type": "percent", "default": 0.02, "min": 0},
        {"id": "ma_exit_period", "label": "均线出场周期", "type": "int", "default": 5, "min": 1},
    ],
}

EXECUTION_BACKEND = "minute_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = None
ALERTS = []
