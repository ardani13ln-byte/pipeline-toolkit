"""Test suite for datakit. Run with: python -m unittest discover -s tests -v"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datakit.convert import (  # noqa: E402
    ConvertError,
    RowError,
    csv_to_json,
    csv_to_jsonl,
    infer_scalar,
    json_to_csv,
    json_to_xml,
    xml_to_json,
)


def write(tmpdir, name, content):
    path = Path(tmpdir) / name
    path.write_text(content, encoding="utf-8")
    return path


class TestInferScalar(unittest.TestCase):
    def test_numeric_conversions(self):
        self.assertEqual(infer_scalar("42"), 42)
        self.assertEqual(infer_scalar("-7"), -7)
        self.assertEqual(infer_scalar("3.5"), 3.5)
        self.assertEqual(infer_scalar("1e3"), 1000.0)

    def test_booleans_but_not_numeric_flags(self):
        self.assertIs(infer_scalar("true"), True)
        self.assertIs(infer_scalar("YES"), True)
        self.assertIs(infer_scalar("off"), False)
        # "1" and "0" must stay numbers, not booleans
        self.assertEqual(infer_scalar("1"), 1)
        self.assertEqual(infer_scalar("0"), 0)

    def test_null_tokens(self):
        for token in ("", "  ", "NULL", "n/a", "-", "None"):
            self.assertIsNone(infer_scalar(token))

    def test_leading_zero_identifiers_preserved(self):
        self.assertEqual(infer_scalar("007"), 7)
        self.assertEqual(infer_scalar("1-2"), "1-2")
        self.assertEqual(infer_scalar("12345678901234567890"), 12345678901234567890)

    def test_no_infer_keeps_strings(self):
        self.assertEqual(infer_scalar("42", infer=False), "42")
        self.assertEqual(infer_scalar("", infer=False), None)


class TestCsvToJson(unittest.TestCase):
    def test_basic_conversion_with_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "in.csv", "id,name,score\n1,Ana,9.5\n2,Bob,7\n")
            rows = csv_to_json(path)
        self.assertEqual(
            rows,
            [
                {"id": 1, "name": "Ana", "score": 9.5},
                {"id": 2, "name": "Bob", "score": 7},
            ],
        )

    def test_quoted_fields_with_embedded_delimiter_and_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "q.csv", 'id,note\n1,"a,b"\n2,"line1\nline2"\n')
            rows = csv_to_json(path)
        self.assertEqual(rows[0]["note"], "a,b")
        self.assertEqual(rows[1]["note"], "line1\nline2")

    def test_semicolon_delimiter_is_sniffed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "s.csv", "id;name\n1;Ana\n")
            rows = csv_to_json(path)
        self.assertEqual(rows, [{"id": 1, "name": "Ana"}])

    def test_explicit_delimiter_overrides_sniff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "p.csv", "id|name\n1|Ana\n")
            rows = csv_to_json(path, delimiter="|")
        self.assertEqual(rows, [{"id": 1, "name": "Ana"}])

    def test_bom_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.csv"
            path.write_bytes("id,name\n1,Ana\n".encode("utf-8-sig"))
            rows = csv_to_json(path)
        self.assertEqual(rows[0], {"id": 1, "name": "Ana"})

    def test_blank_lines_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "bl.csv", "id,name\n1,Ana\n\n\n2,Bob\n")
            rows = csv_to_json(path)
        self.assertEqual(len(rows), 2)

    def test_ragged_row_reports_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "r.csv", "id,name\n1,Ana\n2\n")
            with self.assertRaises(RowError) as ctx:
                csv_to_json(path)
        self.assertEqual(ctx.exception.line, 3)
        self.assertIn("2 fields", str(ctx.exception))

    def test_duplicate_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "d.csv", "id,id\n1,2\n")
            with self.assertRaises(ConvertError) as ctx:
                csv_to_json(path)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_missing_required_column_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "m.csv", "id,name\n1,Ana\n")
            with self.assertRaises(ConvertError) as ctx:
                csv_to_json(path, required=["email"])
        self.assertIn("email", str(ctx.exception))

    def test_empty_input_is_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "e.csv", "")
            with self.assertRaises(ConvertError) as ctx:
                csv_to_json(path)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_jsonl_streaming_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "in.csv", "id,name\n1,Ana\n2,Bob\n")
            lines = list(csv_to_jsonl(path))
        parsed = [json.loads(line) for line in lines]
        self.assertEqual(parsed[0], {"id": 1, "name": "Ana"})
        self.assertEqual(len(lines), 2)


class TestJsonToCsv(unittest.TestCase):
    def test_flat_records(self):
        out = json_to_csv([{"id": 1, "name": "Ana"}, {"id": 2, "name": "Bob"}])
        self.assertEqual(out.splitlines()[0], "id,name")
        self.assertEqual(out.splitlines()[1], "1,Ana")

    def test_nested_flattened_with_dots(self):
        out = json_to_csv([{"user": {"name": "Ana", "id": 7}}])
        self.assertEqual(out.splitlines()[0], "user.name,user.id")
        self.assertEqual(out.splitlines()[1], "Ana,7")

    def test_missing_keys_become_empty_cells(self):
        out = json_to_csv([{"id": 1}, {"id": 2, "extra": "x"}])
        lines = out.splitlines()
        self.assertEqual(lines[0], "id,extra")
        self.assertEqual(lines[1], "1,")

    def test_column_order_is_stable_across_records(self):
        out = json_to_csv([{"b": 1}, {"a": 2, "b": 3}])
        self.assertEqual(out.splitlines()[0], "b,a")

    def test_explicit_column_order_is_honoured(self):
        out = json_to_csv([{"b": 1, "a": 2}], columns=["a", "b"])
        self.assertEqual(out.splitlines()[0], "a,b")

    def test_booleans_and_nulls_render_readably(self):
        out = json_to_csv([{"ok": True, "bad": False, "missing": None}])
        self.assertEqual(out.splitlines()[1], "true,false,")

    def test_wrapped_payload_key_is_detected(self):
        out = json_to_csv({"data": [{"id": 1}]})
        self.assertEqual(out.splitlines(), ["id", "1"])

    def test_invalid_json_reports_position(self):
        with self.assertRaises(ConvertError) as ctx:
            json_to_csv("{not json}")
        self.assertIn("line", str(ctx.exception))


class TestXml(unittest.TestCase):
    def test_repeated_siblings_become_records(self):
        xml = "<items><item><id>1</id><name>Ana</name></item><item><id>2</id><name>Bob</name></item></items>"
        records = xml_to_json(xml)
        self.assertEqual(records, [{"id": "1", "name": "Ana"}, {"id": "2", "name": "Bob"}])

    def test_nested_elements_become_objects(self):
        xml = "<order><id>7</id><cust><name>Ana</name></cust></order>"
        records = xml_to_json(xml)
        self.assertEqual(records[0]["cust"], {"name": "Ana"})

    def test_explicit_record_tag(self):
        xml = "<catalog><row><id>1</id></row><row><id>2</id></row></catalog>"
        self.assertEqual(xml_to_json(xml, record_tag="row"), [{"id": "1"}, {"id": "2"}])

    def test_invalid_xml_reports_line(self):
        with self.assertRaises(ConvertError) as ctx:
            xml_to_json("<a><b></a>")
        self.assertIn("invalid XML", str(ctx.exception))

    def test_json_to_xml_roundtrip(self):
        doc = json_to_xml([{"id": 1, "ok": True, "note": "hi"}], root="rows", record="row")
        self.assertTrue(doc.startswith("<rows>"))
        self.assertIn("<id>1</id>", doc)
        self.assertIn("<ok>true</ok>", doc)

    def test_xml_tag_with_digit_prefix_is_sanitised(self):
        doc = json_to_xml([{"2024_total": 5}], root="r", record="i")
        self.assertIn("<_2024_total>", doc)

    def test_json_xml_json_cycle_preserves_values(self):
        original = [{"id": 1, "name": "Ana", "tags": ["x", "y"]},
                    {"id": 2, "name": "Bob", "tags": []}]
        records = xml_to_json(json_to_xml(original, root="rows", record="row"))
        self.assertEqual(records[0]["id"], "1")
        self.assertEqual(records[0]["tags"], ["x", "y"])
        self.assertEqual(records[1]["name"], "Bob")

    def test_single_record_root_is_itself_the_record(self):
        self.assertEqual(
            xml_to_json(json_to_xml([{"id": 1}], root="rows", record="row")),
            [{"row": {"id": "1"}}],
        )

    def test_two_records_are_detected_without_a_record_tag(self):
        doc = json_to_xml([{"id": 1}, {"id": 2}], root="rows", record="row")
        self.assertEqual(xml_to_json(doc), [{"id": "1"}, {"id": "2"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)