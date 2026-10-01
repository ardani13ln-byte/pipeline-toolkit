# datakit

Zero-dependency converters between **CSV, JSON, JSONL and XML**. No pip install,
no lockfile, Python 3.10+.

```bash
git clone https://github.com/ardani13ln-byte/pipeline-toolkit
cd pipeline-toolkit
python3 -m datakit csv jsonl < orders.csv
```

Built for the boring, recurring part of ETL work: moving data between flat
shapes **without losing rows, mangling types, or silently dropping the malformed
records** somebody notices three weeks later.

## Why it exists

Every "convert CSV to JSON" snippet on the internet gets one of these wrong:

| Naive behaviour | What datakit does |
|---|---|
| `007` becomes `7`, `1-2` becomes a date | Only unambiguous types convert; identifiers survive |
| `"1"` / `"0"` become booleans | They stay integers, because that's what they are |
| A short row silently pads to `None` | `RowError` naming the exact line |
| Duplicate headers overwrite each other | Rejected with both names listed |
| A missing column surfaces as `undefined` three layers later | `--required email` fails immediately, listing the real header |
| Sniffing the delimiter eats the first data row | Prefix is chained back in, so piped `stdin` works |
| `100 ETH` and `$48k` "bounties" get treated as income | *(that one was a client, not this library)* |

## Install

```bash
pipx install .            # or: pip install .
python3 -m datakit --help # works from a clone with zero setup too
```

Python 3.10+, no runtime dependencies. `pip install pytest` only if you want
pytest; the suite ships with `unittest`.

## CLI

```
python3 -m datakit <from> <to> [options]

  -i, --input PATH     input file, or - for stdin (default)
  -o, --output PATH    output file, or - for stdout (default)
  -d, --delimiter CH   CSV delimiter (default: sniffed)
      --required LIST  comma-separated columns that must exist
      --columns LIST   preferred CSV column order
      --record-tag T   XML tag wrapping each record
      --root NAME      XML root element (default: records)
      --record NAME    XML per-record element (default: record)
      --no-infer       keep every cell as a string
      --no-flatten     do not flatten nested JSON into dotted columns
      --stream         favour streaming over pretty printing
```

All 12 format pairs are supported: `csv|json|jsonl|xml` → `csv|json|jsonl|xml`.

### Examples

```bash
# Semicolon CSV exported from a European Excel, straight to JSON Lines
python3 -m datakit csv jsonl < export.csv --delimiter ';'

# Nested API payload to a spreadsheet, columns in your preferred order
echo '{"results":[{"user":{"name":"Ana"},"total":12}]}' \
  | python3 -m datakit json csv --columns total,user.name

# API response to XML, for a partner who still speaks XML
curl -s https://api.example.com/orders \
  | python3 -m datakit json xml --root orders --record order

# Validate a supplier feed before you build anything on top of it
python3 -m datakit csv json --required sku,qty,price < supplier.csv
```

Exit codes: `0` success, `2` bad input (message on stderr, with line number),
`3` unexpected internal error. Never a traceback.

## Library

```python
from datakit import csv_to_json, csv_to_jsonl, json_to_csv, json_to_xml, xml_to_json

rows = csv_to_json("orders.csv", required=["id", "total"])
print(rows[0]["total"])                     # 149.9  -> float, not "149.90"

for line in csv_to_jsonl("huge.csv"):       # constant memory
    index.send(line)

csv_text = json_to_csv(payload, flatten=True, columns=["id", "user.name"])
records  = xml_to_json("<items><item>...</item></items>", record_tag="item")
doc      = json_to_xml(records, root="rows", record="row")
```

Every function accepts a **path, a file object, or a string payload**, so the
same call works in a script, a web request handler and a unit test.

## Type inference rules

| Input | Result | Why |
|---|---|---|
| `42`, `-7` | `int` | unambiguous |
| `3.5`, `1e3` | `float` | unambiguous |
| `true`, `YES`, `off` | `bool` | only word forms, not `1`/`0` |
| `1`, `0` | `int` | digits are numbers, not flags |
| ``, `null`, `N/A`, `-` | `None` | configurable via `null_tokens` |
| `007`, `1-2`, `12345678901234567890` | `str` / `int` | never silently reinterpreted |
| anything else | `str` | `--no-infer` disables the table above |

Nested structures flatten to dotted keys (`user.address.city`); scalar lists
join with the separator (`tags` → `a.b.c`); repeated XML siblings become JSON
lists.

## Tests

```bash
python3 -m unittest discover -s tests -v      # 46 tests, ~1s
```

Coverage includes the cases naive converters get wrong: embedded delimiters and
newlines in quoted fields, BOM'd files, ragged rows, duplicate headers,
non-seekable `stdin`, missing required columns, XML with single vs repeated
roots, tag names starting with a digit, and JSON→XML→JSON roundtrips.

## Design notes

- **Streaming where it matters.** `csv_to_jsonl` yields one line at a time and
  never materialises the file, so it handles inputs larger than RAM.
- **Non-seekable input is a first-class case.** Delimiter sniffing reads a
  two-line prefix and chains it back into the iterator instead of seeking, so
  `stdin` from a pipe behaves identically to a file.
- **Errors carry coordinates.** `RowError.line` is the 1-based source line, not
  a record index, because that is what you need to open the file in an editor.
- **No dependencies, on purpose.** This is the kind of tool that sits inside
  someone else's cron job for six years. It should never need a resolver.

## License

MIT