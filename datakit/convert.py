"""Format conversion primitives.

Every public function here follows the same contract:

* input is a path (str/Path) or an already-open text file object
* rows are dicts keyed by column/field name
* problems raise :class:`ConvertError` carrying the offending line number,
  because a converter that says "invalid data" is useless at 2am
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

__all__ = [
    "ConvertError",
    "RowError",
    "csv_to_json",
    "csv_to_jsonl",
    "infer_scalar",
    "json_to_csv",
    "json_to_xml",
    "xml_to_json",
]

DEFAULT_NULL_TOKENS = ("", "null", "none", "nil", "n/a", "na", "-", "\\n")

_TRUE_TOKENS = {"true", "t", "yes", "y", "on", "1"}
_FALSE_TOKENS = {"false", "f", "no", "n", "off", "0"}

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\- ]*$")


class ConvertError(Exception):
    """Conversion failed in a way the caller cannot recover from."""


class RowError(ConvertError):
    """A single record is unusable. Carries its 1-based source line."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


class _FixedDialect(csv.Dialect):
    """csv.excel with a caller-chosen delimiter, as a real Dialect class."""

    delimiter = ","
    quotechar = '"'
    escapechar = None
    doublequote = True
    skipinitialspace = False
    lineterminator = "\r\n"
    quoting = csv.QUOTE_MINIMAL
    strict = False


def is_inline_content(source: Any) -> bool:
    """True when a ``str`` argument is document text rather than a filename.

    Paths and payloads are both strings, so disambiguate once instead of at
    every call site: anything with a newline, or that opens like markup or a
    JSON container, is content. Everything else must exist on disk.
    """
    if not isinstance(source, str):
        return False
    if "\n" in source or "\r" in source:
        return True
    return source.lstrip()[:1] in ("<", "{", "[")


def _open(source: Any) -> tuple[Any, bool]:
    """Return (handle, should_close) for a path, a text payload or a file object."""
    if is_inline_content(source):
        return io.StringIO(source), True
    if isinstance(source, (str, os.PathLike)):
        try:
            return open(source, "r", encoding="utf-8-sig", newline=""), True
        except OSError as exc:
            raise ConvertError(f"cannot read {source}: {exc}") from exc
    return source, False


def _read_text_source(source: Any) -> str:
    """Read any accepted input into text."""
    if isinstance(source, bytes):
        return source.decode("utf-8-sig")
    if isinstance(source, str) and not is_inline_content(source) and os.path.exists(source):
        try:
            return Path(source).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ConvertError(f"cannot read {source}: {exc}") from exc
    handle, close = _open(source)
    try:
        return handle.read()
    finally:
        if close:
            handle.close()


def infer_scalar(
    text: str,
    null_tokens: Iterable[str] = DEFAULT_NULL_TOKENS,
    infer: bool = True,
) -> Any:
    """Turn one CSV cell into a Python value.

    Only unambiguous cases are converted: integers, floats, booleans and the
    configured null tokens. Anything else stays a string, because turning
    ``"007"`` into ``7`` or ``"1-2"`` into a date is how pipelines lose money.

    Leading/trailing whitespace is significant for the null test but the cell
    is returned stripped, matching what spreadsheet users expect.
    """
    if text is None:
        return None
    stripped = text.strip()
    if stripped.lower() in {t.lower() for t in null_tokens}:
        return None
    if not infer:
        return text
    lowered = stripped.lower()
    if lowered in _TRUE_TOKENS and lowered not in {"1", "0"}:
        return True
    if lowered in _FALSE_TOKENS and lowered not in {"1", "0"}:
        return False
    if _INT_RE.match(stripped):
        return int(stripped)
    if _FLOAT_RE.match(stripped) and not _INT_RE.match(stripped):
        return float(stripped)
    return text


def _sniff_dialect(sample: str, delimiter: str | None) -> csv.Dialect | type[csv.Dialect]:
    if delimiter:
        dialect = _FixedDialect()
        dialect.delimiter = delimiter
        return dialect
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _read_rows(
    handle: io.TextIOBase,
    delimiter: str | None,
    required: Iterable[str],
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (line_number, row) from a CSV stream, raising on bad rows."""
    # Read a small prefix for delimiter sniffing, then chain it back in front of
    # the stream: seeking would work for files but not for pipes like stdin.
    prefix: list[str] = []
    for _ in range(2):
        line = handle.readline()
        if not line:
            break
        prefix.append(line)
    if not prefix or not prefix[0].strip():
        raise ConvertError("input is empty: expected a header row")
    dialect = _sniff_dialect("".join(prefix), delimiter)
    columns = next(csv.reader([prefix[0]], dialect=dialect))
    columns = [c.strip() for c in columns]
    if not columns or not any(columns):
        raise ConvertError("header row has no usable column names")
    dupes = {c for c in columns if columns.count(c) > 1}
    if dupes:
        raise ConvertError(f"duplicate column names: {', '.join(sorted(dupes))}")

    missing = [c for c in required if c not in columns]
    if missing:
        raise ConvertError(
            f"required column(s) not found: {', '.join(missing)}; header is: "
            f"{', '.join(columns)}"
        )

    reader = csv.reader(itertools.chain(prefix[1:], handle), dialect=dialect)
    width = len(columns)
    for row in reader:
        if not row or (len(row) == 1 and not row[0].strip()):
            continue  # blank line, not a record
        line = reader.line_num + 1
        if len(row) != width:
            raise RowError(
                line, f"expected {width} fields (header width) but found {len(row)}"
            )
        yield line, dict(zip(columns, row))


def csv_to_json(
    source: str | os.PathLike[str] | io.TextIOBase,
    *,
    delimiter: str | None = None,
    infer: bool = True,
    null_tokens: Iterable[str] = DEFAULT_NULL_TOKENS,
    required: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Read a CSV file into a list of typed dicts. Raises on the first bad row."""
    handle, close = _open(source)
    try:
        out: list[dict[str, Any]] = []
        for _, row in _read_rows(handle, delimiter, required):
            out.append(
                {k: infer_scalar(v, null_tokens, infer) for k, v in row.items()}
            )
        return out
    finally:
        if close:
            handle.close()


def csv_to_jsonl(
    source: str | os.PathLike[str] | io.TextIOBase,
    *,
    delimiter: str | None = None,
    infer: bool = True,
    null_tokens: Iterable[str] = DEFAULT_NULL_TOKENS,
    required: Iterable[str] = (),
) -> Iterator[str]:
    """Stream a CSV as JSON Lines.

    Memory stays flat regardless of file size, which is the whole point of
    JSONL. Nothing is buffered until the generator is consumed.
    """
    handle, close = _open(source)
    try:
        for line, row in _read_rows(handle, delimiter, required):
            yield json.dumps(
                {k: infer_scalar(v, null_tokens, infer) for k, v in row.items()},
                ensure_ascii=False,
                sort_keys=True,
            )
    finally:
        if close:
            handle.close()


def _flatten(obj: Any, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """Turn nested dicts/lists into dotted keys, for CSV output."""
    flat: dict[str, Any] = {}
    if isinstance(obj, Mapping):
        if not obj and prefix:
            flat[prefix] = ""
        for key, value in obj.items():
            name = f"{prefix}{sep}{key}" if prefix else str(key)
            flat.update(_flatten(value, name, sep))
    elif isinstance(obj, list):
        if obj and all(not isinstance(v, (Mapping, list)) for v in obj):
            flat[prefix] = sep.join(_scalar_text(v) for v in obj)
        elif not obj:
            flat[prefix] = ""
        else:
            for index, value in enumerate(obj):
                flat.update(_flatten(value, f"{prefix}{sep}{index}", sep))
    else:
        flat[prefix] = _scalar_text(obj)
    return flat


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def json_to_csv(
    source: str | os.PathLike[str] | io.TextIOBase | Iterable[Any],
    *,
    flatten: bool = True,
    columns: Iterable[str] | None = None,
    sep: str = ".",
    null_text: str = "",
) -> str:
    """Render JSON (array of objects, or object-of-arrays) as CSV text.

    Column order follows first appearance unless ``columns`` is given, so the
    output header is stable across runs. Extra keys are appended, not dropped.
    """
    records = _records_from(source)
    if not records:
        return ""

    if flatten:
        prepared = [_flatten(r, sep=sep) if isinstance(r, Mapping) else {"value": r} for r in records]
    else:
        prepared = [r if isinstance(r, Mapping) else {"value": r} for r in records]

    if columns:
        ordered = list(columns)
        extras = sorted({k for r in prepared for k in r} - set(ordered))
        ordered += extras
    else:
        seen: dict[str, None] = {}
        for record in prepared:
            for key in record:
                seen.setdefault(key, None)
        ordered = list(seen)

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=ordered, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for record in prepared:
        writer.writerow(
            {k: (null_text if record.get(k) is None else record.get(k, null_text))
             for k in ordered}
        )
    return buffer.getvalue()


def _records_from(source: Any) -> list[Any]:
    """Normalise the many shapes JSON arrives in into a list of records."""
    if isinstance(source, (str, bytes)) or hasattr(source, "read"):
        source = _read_text_source(source)
    if isinstance(source, str):
        text = source.strip()
        if not text:
            return []
        try:
            source = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConvertError(
                f"input is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
            ) from exc
    if isinstance(source, Mapping):
        # {"items": [...]} or {"a": 1, "b": 2} - the latter is a single record
        for key in ("items", "results", "data", "rows", "records"):
            if key in source and isinstance(source[key], list):
                return source[key]
        return [source]
    if isinstance(source, list):
        return source
    raise ConvertError(f"cannot interpret {type(source).__name__} as tabular data")


def _coerce_tag(tag: str) -> str:
    """XML tags cannot start with a digit; keep the name readable."""
    cleaned = re.sub(r"[^\w.\-]", "_", tag)
    if not cleaned or not re.match(r"^[A-Za-z_]", cleaned):
        cleaned = f"_{cleaned.lstrip('_')}" if cleaned.strip("_") else "_item"
    return cleaned


def _element_to_obj(element: ET.Element) -> Any:
    """Recursively convert an element, collapsing repeated tags into lists."""
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return text if text else None
    grouped: dict[str, list[Any]] = {}
    for child in children:
        grouped.setdefault(_coerce_tag(child.tag), []).append(_element_to_obj(child))
    return {tag: values[0] if len(values) == 1 else values for tag, values in grouped.items()}


def xml_to_json(
    source: str | os.PathLike[str] | io.TextIOBase,
    *,
    record_tag: str | None = None,
) -> list[Any]:
    """Flatten an XML document into JSON-ready records.

    Without ``record_tag`` the parser picks the child tag that repeats most
    under the root, which is right for the usual ``<items><item/><item/></items>``
    shape. Nested elements become objects; repeated siblings become lists.
    """
    handle, close = _open(source)
    try:
        try:
            root = ET.parse(handle).getroot()
        except ET.ParseError as exc:
            line = getattr(exc, "position", (0, 0))[0]
            raise ConvertError(f"line {line}: invalid XML: {exc}") from exc
    finally:
        if close:
            handle.close()

    if record_tag:
        records = [
            _element_to_obj(node)
            for node in root.findall(record_tag)
        ]
        if not records:
            records = [_element_to_obj(node) for node in root.iter(record_tag)]
    else:
        counts: dict[str, int] = {}
        for node in root:
            counts[_coerce_tag(node.tag)] = counts.get(_coerce_tag(node.tag), 0) + 1
        if not counts:
            return [_element_to_obj(root)]
        tag = max(counts, key=lambda t: counts[t])
        if counts[tag] == 1:
            # No sibling repeats, so the root itself is the one record:
            # <order><id>7</id><cust>...</cust></order> must not collapse to
            # the value of <id>.
            return [_element_to_obj(root)]
        records = [_element_to_obj(node) for node in root if _coerce_tag(node.tag) == tag]
    return records


def _append(parent: ET.Element, key: str, value: Any) -> None:
    tag = _coerce_tag(key)
    if isinstance(value, Mapping):
        node = ET.SubElement(parent, tag)
        for sub_key, sub_value in value.items():
            _append(node, str(sub_key), sub_value)
    elif isinstance(value, list):
        for item in value:
            _append(parent, tag, item)
    elif isinstance(value, bool):
        ET.SubElement(parent, tag).text = "true" if value else "false"
    elif value is not None:
        ET.SubElement(parent, tag).text = _scalar_text(value)


def json_to_xml(
    source: str | os.PathLike[str] | io.TextIOBase | Iterable[Any],
    *,
    root: str = "records",
    record: str = "record",
    indent: str | None = "  ",
) -> str:
    """Render JSON records as a readable XML document."""
    records = _records_from(source)
    tree = ET.Element(_coerce_tag(root))
    for row in records:
        node = ET.SubElement(tree, _coerce_tag(record))
        if isinstance(row, Mapping):
            for key, value in row.items():
                _append(node, str(key), value)
        else:
            node.text = _scalar_text(row)
    if indent:
        ET.indent(tree, space=indent)
    return ET.tostring(tree, encoding="unicode", xml_declaration=False)