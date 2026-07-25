# All-Minute-Data Normalization Design

## Goal

Materialize every usable local A-share minute Parquet file into a durable TickFlow-ready library at:

`F:\quant\data\minute_1min_tickflow_merged`

The source directories remain read-only. The output retains all available history, uses one canonical file per symbol, supports interrupted/resumed runs, and records incomplete or corrupt source coverage explicitly.

## Source Inventory

- Current source: `F:\quant\data\minute_1min_pytdx`
- Historical source: `F:\quant\data\_incoming_a_share_pytdx\minute_1min_pytdx_hist`
- Canonical filenames in the union: 5,865
- Files with both history and current inputs: 4,713
- Current-only canonical filenames: 1,152
- Rejected noncanonical input: `T600018_SH.parquet`
- Current rows: 323,658,316
- Historical rows reported by readable files: 2,679,789,555
- Empty current placeholders: 336
- Empty historical placeholders: 384
- Corrupt historical input: `603838_SH.parquet`

The current `603838_SH.parquet` is healthy and contains 36,838 rows, so its canonical output will contain current data and carry a history-corruption warning.

## Approaches Considered

### Separate materialized library (selected)

Merge into a new directory while preserving both source trees. Future backtests read one directory with no repeated merge cost. This uses additional disk space but has the safest recovery and rollback behavior.

### In-place replacement (rejected)

Overwrite the current files with merged data. This saves disk space but destroys the clean source boundary and makes interrupted writes or logic mistakes harder to recover from.

### Virtual two-source TickFlow loader (rejected)

Teach TickFlow to read and merge the two directories for every backtest. This avoids materialization but makes every future run slower and introduces a permanent application-code dependency on the current source layout.

## Canonical Output

Each canonical filename matches `NNNNNN_(SZ|SH|BJ).parquet` and contains these columns in order:

| Column | Type |
|---|---|
| `ts_code` | UTF-8 string |
| `trade_time` | UTF-8 ISO datetime string |
| `open` | Float64 |
| `high` | Float64 |
| `low` | Float64 |
| `close` | Float64 |
| `vol` | Int64 |
| `amount` | Float64 |

Rows are sorted by `trade_time` and unique by `(ts_code, trade_time)`. If history and current inputs overlap, the current input wins. Empty source placeholders become zero-row files with the canonical typed schema.

## Directory Layout

```text
F:\quant\data\minute_1min_tickflow_merged\
  000001_SZ.parquet
  ...
  920xxx_BJ.parquet
  manifest.json
  rejected_inputs.json
  .normalization\
    state.jsonl
    run.log
```

Temporary files use a `.parquet.partial` suffix in the output directory and are never considered valid output.

## Conversion Flow

1. Inventory canonical filenames from the current directory. Historical inputs are matched by exact filename.
2. Capture source fingerprints from absolute path, size, and nanosecond modification time.
3. Read the last state record for each filename. Skip only when fingerprints match and the existing output footer, row count, and schema validate.
4. Read current input and readable history input, select the eight columns, and cast to canonical types.
5. Concatenate, sort, and deduplicate. Validate symbol identity, time ordering, and duplicate count in memory.
6. Write a Zstandard-compressed `.partial` Parquet with statistics, validate its footer, then atomically replace the final file.
7. Append a flushed state record after the final file is valid. A crash before this record causes a safe reprocess on resume.
8. At completion, compact latest state into `manifest.json` and write noncanonical source names to `rejected_inputs.json`.

Processing is sequential by filename. Both reads and writes use F:, so concurrent workers would introduce random I/O contention and increase peak memory without a reliable throughput benefit.

## Coverage States

- `merged_history_current`: both populated sources used.
- `current_only_no_history`: no historical filename exists.
- `current_only_history_empty`: historical placeholder has zero rows.
- `history_only_current_empty`: current placeholder has zero rows.
- `empty_both`: both inputs have zero rows.
- `empty_current_only`: current-only placeholder has zero rows.
- `current_only_history_corrupt`: history could not be read; current data retained.

Every manifest entry includes source fingerprints, source row counts, output rows/bytes, first and last timestamps, duplicate rows removed, coverage state, and warnings.

## Error Handling

- A corrupt history file is recorded as a warning and current data is retained.
- A corrupt or unreadable current file prevents a valid canonical output for that symbol. The run records the failure, continues other symbols, and exits nonzero.
- A symbol value that disagrees with its canonical filename is a failure; the tool does not silently rewrite identity.
- Existing output with stale fingerprints is regenerated atomically.
- Existing `.partial` files are ignored and replaced when that symbol is processed.

## Verification

The run is complete only when:

- 5,865 canonical output Parquet files exist.
- Every output footer is readable and every schema is canonical, including empty files.
- Manifest totals match output metadata totals and output byte totals.
- Each processed populated file was verified sorted and duplicate-free during conversion.
- Coverage states account for every canonical input filename and the one rejected filename.
- TickFlow's `LocalMinuteParquetRepository` successfully loads representative merged, current-only, empty, and corrupt-history-recovery outputs.
- A broad deterministic sample across markets and coverage states loads through TickFlow without schema or datetime errors.

## Non-Goals

- Recovering history that is absent from the truncated local archive.
- Downloading replacement data from an external provider.
- Deleting or modifying either source directory.
- Changing TickFlow production code or automatically switching its configured data directory.
