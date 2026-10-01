"""End-to-end CLI tests: real files in, real files out, real exit codes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = "id,name,score\n1,Ana,9.5\n2,Bob,7\n"


def run(args, stdin=None, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "datakit", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
    )


class TestCli(unittest.TestCase):
    def test_csv_to_json_on_stdin(self):
        proc = run(["csv", "json"], stdin=CSV)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            [{"id": 1, "name": "Ana", "score": 9.5}, {"id": 2, "name": "Bob", "score": 7}],
        )

    def test_json_to_csv_on_stdin(self):
        proc = run(["json", "csv"], stdin='[{"id":1,"name":"Ana"}]')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().splitlines(), ["id,name", "1,Ana"])

    def test_file_input_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.csv"
            dst = Path(tmp) / "out.json"
            src.write_text(CSV, encoding="utf-8")
            proc = run(["csv", "json", "-i", str(src), "-o", str(dst)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(json.loads(dst.read_text())), 2)

    def test_jsonl_output_is_one_object_per_line(self):
        proc = run(["csv", "jsonl"], stdin=CSV)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["name"], "Ana")

    def test_required_column_failure_exits_2_with_message(self):
        proc = run(["csv", "json", "--required", "email"], stdin=CSV)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("email", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_malformed_row_failure_names_the_line(self):
        proc = run(["csv", "json"], stdin="id,name\n1,Ana\n2\n")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("line 3", proc.stderr)

    def test_xml_pipeline(self):
        proc = run(["xml", "json"], stdin="<items><item><id>1</id></item><item><id>2</id></item></items>")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [{"id": "1"}, {"id": "2"}])

    def test_json_to_xml_pipeline(self):
        proc = run(["json", "xml", "--root", "rows", "--record", "row"],
                   stdin='[{"id":1}]')
        self.assertIn("<id>1</id>", proc.stdout)

    def test_identical_formats_are_rejected(self):
        proc = run(["json", "json"], stdin="[]")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("identical", proc.stderr)

    def test_missing_input_file_exits_2(self):
        proc = run(["csv", "json", "-i", "/nonexistent/file.csv"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot read", proc.stderr)

    def test_no_infer_keeps_strings(self):
        proc = run(["csv", "json", "--no-infer"], stdin=CSV)
        first = json.loads(proc.stdout)[0]
        self.assertEqual(first["id"], "1")

    def test_pipeline_composes_with_shell_tools(self):
        # Real shell pipe: stdin must work even though stdin is not seekable.
        script = 'printf "id,name,score\\n1,Ana,9.5\\n2,Bob,7\\n" | python3 -m datakit csv jsonl'
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["name"], "Ana")

    def test_roundtrip_through_a_temp_file_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.csv"
            mid = Path(tmp) / "b.jsonl"
            src.write_text(CSV, encoding="utf-8")
            first = run(["csv", "jsonl", "-i", str(src), "-o", str(mid)])
            self.assertEqual(first.returncode, 0, first.stderr)
            second = run(["jsonl", "csv", "-i", str(mid)])
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.strip().splitlines()[0], "id,name,score")


if __name__ == "__main__":
    unittest.main(verbosity=2)