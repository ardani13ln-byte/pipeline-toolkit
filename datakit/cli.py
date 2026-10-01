"""Command line interface: ``python -m datakit <from> <to> [options]``.

Design notes worth knowing before reading:

* stdin/stdout are the defaults, so it composes in a shell pipeline
* ``--stream`` turns any conversion into a memory-flat one where it makes sense
* errors exit 2 with the offending line number, never a traceback
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .convert import (
    ConvertError,
    csv_to_json,
    csv_to_jsonl,
    json_to_csv,
    json_to_xml,
    xml_to_json,
)

FORMATS = ("csv", "json", "jsonl", "xml")


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConvertError(f"cannot read {path}: {exc}") from exc


def _read_json_lines(text: str) -> list[Any]:
    import json

    records = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ConvertError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    return records


def _write(text: str, out: str) -> None:
    if out == "-":
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        Path(out).write_text(text, encoding="utf-8")


def _convert(args: argparse.Namespace) -> str:
    src, dst = args.source_format, args.target_format
    text = _read_text(args.input)

    if src == "csv":
        tmp = Path(args.input) if args.input != "-" else None
        handle = tmp.open("r", encoding="utf-8-sig", newline="") if tmp else None
        try:
            source: Any = handle if handle is not None else text
            if dst in ("json", "jsonl"):
                import json as _json

                if dst == "jsonl" or args.stream:
                    # csv_to_jsonl already emits JSON text; do not re-encode.
                    return "\n".join(
                        csv_to_jsonl(
                            source,
                            delimiter=args.delimiter,
                            infer=not args.no_infer,
                            required=args.required,
                        )
                    )
                return _json.dumps(
                    csv_to_json(
                        source,
                        delimiter=args.delimiter,
                        infer=not args.no_infer,
                        required=args.required,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            if dst == "xml":
                import json as _json

                return json_to_xml(_json.loads(text or "[]"), root=args.root, record=args.record)
        finally:
            if handle is not None:
                handle.close()

    if src in ("json", "jsonl"):
        import json as _json

        payload: Any = (
            _read_json_lines(text) if src == "jsonl" else _json.loads(text or "null")
        )
        if dst == "csv":
            return json_to_csv(payload, flatten=not args.no_flatten, columns=args.columns)
        if dst == "xml":
            return json_to_xml(payload, root=args.root, record=args.record)
        if dst == "jsonl":
            lines = [_json.dumps(row, ensure_ascii=False, sort_keys=True) for row in _records(payload)]
            return "\n".join(lines)

    if src == "xml":
        import json as _json

        records = xml_to_json(text, record_tag=args.record_tag)
        if dst == "json":
            return _json.dumps(records, ensure_ascii=False, indent=2)
        if dst == "jsonl":
            return "\n".join(_json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
        if dst == "csv":
            return json_to_csv(records, flatten=not args.no_flatten, columns=args.columns)

    raise ConvertError(f"unsupported conversion: {src} -> {dst}")


def _records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "data", "rows", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datakit",
        description="Convert CSV, JSON, JSONL and XML without losing rows.",
    )
    parser.add_argument("--version", action="version", version=f"datakit {__version__}")
    parser.add_argument("source_format", choices=FORMATS)
    parser.add_argument("target_format", choices=FORMATS)
    parser.add_argument("-i", "--input", default="-", help="input file, or - for stdin")
    parser.add_argument("-o", "--output", default="-", help="output file, or - for stdout")
    parser.add_argument("-d", "--delimiter", help="CSV delimiter (default: sniff)")
    parser.add_argument("--required", type=lambda v: v.split(","),
                        help="comma-separated columns that must exist")
    parser.add_argument("--columns", type=lambda v: v.split(","),
                        help="preferred CSV column order")
    parser.add_argument("--record-tag", help="XML tag that wraps each record")
    parser.add_argument("--root", default="records", help="XML root element name")
    parser.add_argument("--record", default="record", help="XML per-record element name")
    parser.add_argument("--no-infer", action="store_true",
                        help="keep every cell as a string")
    parser.add_argument("--no-flatten", action="store_true",
                        help="do not flatten nested JSON into dotted columns")
    parser.add_argument("--stream", action="store_true",
                        help="favour streaming output over pretty printing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source_format == args.target_format:
        parser.error("source and target formats are identical")
    args.required = args.required or ()
    args.columns = args.columns
    try:
        _write(_convert(args), args.output)
    except ConvertError as exc:
        print(f"datakit: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last-resort guard for a CLI
        print(f"datakit: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())