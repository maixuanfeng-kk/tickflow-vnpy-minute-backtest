import zipfile
from pathlib import Path

from app.services.tushare_import import TushareImportSummary, TushareLocalImporter


def test_minute_import_accepts_decimal_amount_after_integer_rows(tmp_path: Path) -> None:
    rows = [
        "ts_code,trade_time,open,close,high,low,vol,amount",
        *[
            f"000001.SZ,2022-01-04 09:{30 + index // 60:02d}:{index % 60:02d},10,10,10,10,100,100"
            for index in range(600)
        ],
        "000001.SZ,2022-01-04 11:10:00,10,10,10,10,100,1992153.5",
    ]
    source = "\n".join(rows).encode()
    summary = TushareImportSummary("minute", "preview", True)
    importer = TushareLocalImporter(tmp_path, tmp_path)

    importer._stage_minute_batch(
        [("sample.csv", source)], tmp_path / "stage", 0, set(), {}, summary, True
    )

    assert summary.files_imported == 1
    assert summary.rows_valid == 601
    assert summary.failed_files == []


def test_minute_import_prefers_annual_zip_when_same_year_directory_exists(tmp_path: Path) -> None:
    minute_root = tmp_path / "A股分钟K"
    minute_root.mkdir()
    (minute_root / "2023").mkdir()
    with zipfile.ZipFile(minute_root / "2023.zip", "w") as archive:
        archive.writestr(
            "2023/000001.SZ/202301.csv",
            "\n".join([
                "ts_code,trade_time,open,close,high,low,vol,amount",
                "000001.SZ,2023-01-03 09:30:00,10,10,10,10,100,1000",
            ]),
        )

    importer = TushareLocalImporter(tmp_path, tmp_path / "data", batch_size=1)

    summary = importer.import_minutes([2023], dry_run=True)

    assert summary.files_discovered == 1
    assert summary.files_imported == 1
    assert summary.rows_valid == 1
