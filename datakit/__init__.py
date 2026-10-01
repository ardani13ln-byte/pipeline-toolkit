"""datakit - zero-dependency converters between CSV, JSON, JSONL and XML.

Built for the boring, recurring part of ETL work: getting data out of one
flat shape and into another without losing rows, mangling types, or silently
dropping the malformed lines that somebody will notice three weeks later.
"""

__version__ = "1.0.0"

from .convert import (
    ConvertError,
    RowError,
    csv_to_json,
    csv_to_jsonl,
    json_to_csv,
    json_to_xml,
    xml_to_json,
    infer_scalar,
)

__all__ = [
    "ConvertError",
    "RowError",
    "csv_to_json",
    "csv_to_jsonl",
    "infer_scalar",
    "json_to_csv",
    "json_to_xml",
    "xml_to_json",
    "__version__",
]