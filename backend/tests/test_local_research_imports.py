from __future__ import annotations

import json

import polars as pl

from app.services.local_daily_pro_import import LocalDailyProCsvImporter
from app.services.local_daily_xbx_import import LocalDailyXbxCsvImporter
from app.services.local_financial_import import LocalFinancialCsvImporter


DAILY_HEADER = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount,total_mv,circ_mv,turnover_rate,volume_ratio,pe,pb,total_share,float_share,free_share\n"


def _daily_csv(path, *, close: str = "11.0") -> None:
    path.write_text(
        DAILY_HEADER + f"000001.SZ,20250102,10,12,9,{close},10,1,10,123,456,1000001,900000,1,2,3,4,5,6,7\n",
        encoding="utf-8",
    )


def test_daily_pro_import_preserves_fields_normalises_units_and_rebuilds_partition(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "2025").mkdir(parents=True)
    file = source / "2025" / "000001_SZ.csv"
    _daily_csv(file)

    importer = LocalDailyProCsvImporter(source, tmp_path / "data", years=[2025], batch_size=1)
    preview = importer.run(dry_run=True)
    assert preview.status == "preview"
    assert not (tmp_path / "data" / "kline_daily_pro").exists()

    summary = importer.run()
    output = tmp_path / "data" / "kline_daily_pro" / "date=2025-01-02" / "part.parquet"
    frame = pl.read_parquet(output)
    assert summary.partitions_rebuilt == 1
    assert frame["symbol"].to_list() == ["000001.SZ"]
    assert frame["volume"].to_list() == [12300.0]
    assert frame["amount"].to_list() == [456000.0]
    assert frame["amount_source"].to_list() == [456.0]
    assert frame["total_mv"].to_list() == [1000001.0]

    _daily_csv(file, close="12.0")
    importer.run()
    assert pl.read_parquet(output)["close"].to_list() == [12.0]
    metadata = json.loads((tmp_path / "data" / "kline_daily_pro" / "_metadata.json").read_text("utf-8"))
    assert metadata["units"]["total_mv"] == "ten-thousand CNY"


def _financial_csv(path) -> None:
    path.write_text(
        "说明行\n"
        "stock_code,statement_format,report_date,publish_date,R_revenue@xbx,R_operating_total_revenue@xbx,R_np@xbx\n"
        "sh600000,一般企业,20250331,2025-04-30,,200,30\n"
        "sz000001,一般企业,20250331,2025-04-30,100,200,20\n",
        encoding="utf-8",
    )


def test_financial_import_archives_raw_and_builds_income_projection(tmp_path) -> None:
    source = tmp_path / "financial"
    source.mkdir()
    _financial_csv(source / "sample.csv")

    importer = LocalFinancialCsvImporter(source, tmp_path / "data", batch_size=1)
    assert importer.run(dry_run=True).status == "preview"
    summary = importer.run()
    income = pl.read_parquet(tmp_path / "data" / "financials" / "income" / "part.parquet").sort("symbol")
    rows = {row["symbol"]: row for row in income.to_dicts()}
    assert summary.income_rows == 2
    assert rows["600000.SH"]["revenue"] == 200.0
    assert rows["600000.SH"]["revenue_source"] == "R_operating_total_revenue@xbx"
    assert rows["000001.SZ"]["net_income"] == 20.0
    assert list((tmp_path / "data" / "financials" / "raw_xbx").glob("batch=*/part.parquet"))


def test_xbx_daily_import_preserves_historical_name_for_st_filtering(tmp_path) -> None:
    source = tmp_path / "xbx"
    source.mkdir()
    (source / "sh600000.csv").write_text(
        "说明行\n"
        "股票代码,股票名称,交易日期,开盘价,最高价,最低价,收盘价,前收盘价,成交量,成交额,流通市值,总市值\n"
        "sh600000,*ST测试,2026-04-30,10,11,9,10,9.5,100,1000,9000000000,10000000001\n",
        encoding="utf-8",
    )
    importer = LocalDailyXbxCsvImporter(source, tmp_path / "data", batch_size=1)
    assert importer.run(dry_run=True).status == "preview"
    summary = importer.run()
    frame = pl.read_parquet(tmp_path / "data" / "kline_daily_xbx" / "date=2026-04-30" / "part.parquet")
    assert summary.rows_valid == 1
    assert frame["symbol"].to_list() == ["600000.SH"]
    assert frame["name"].to_list() == ["*ST测试"]
    assert frame["total_mv"].to_list() == [10_000_000_001.0]
