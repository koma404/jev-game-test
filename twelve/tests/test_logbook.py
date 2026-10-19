"""twelve/logbook.py 的单元测试：日志路径、玩家哈希、写日志 / 打印、百分位与延迟统计。"""
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from twelve.logbook import Logbook, latency, pct


class PathTest(unittest.TestCase):
    def test_path_with_log_dir(self):
        with tempfile.TemporaryDirectory() as d:
            book = Logbook(log_dir=d, echo=False)
            self.assertEqual(book.path, os.path.join(d, "twelve.jsonl"))
            self.assertEqual(os.path.basename(book.path), "twelve.jsonl")

    def test_path_without_log_dir(self):
        self.assertIsNone(Logbook(echo=False).path)
        self.assertIsNone(Logbook(log_dir="", echo=False).path)

    def test_log_dir_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "a", "b")
            Logbook(log_dir=sub, echo=False)
            self.assertTrue(os.path.isdir(sub))


class PlayerTest(unittest.TestCase):
    def test_stable_for_same_ip(self):
        book = Logbook(salt="s1")
        self.assertEqual(book.player("1.2.3.4"), book.player("1.2.3.4"))
        self.assertEqual(book.player("1.2.3.4"), Logbook(salt="s1").player("1.2.3.4"))

    def test_length_and_hex(self):
        h = Logbook(salt="s1").player("1.2.3.4")
        self.assertEqual(len(h), 8)
        int(h, 16)  # 是十六进制

    def test_empty_ip(self):
        book = Logbook(salt="s1")
        self.assertEqual(book.player(""), "?")
        self.assertEqual(book.player(None), "?")

    def test_different_salt_different_result(self):
        self.assertNotEqual(Logbook(salt="s1").player("1.2.3.4"), Logbook(salt="s2").player("1.2.3.4"))

    def test_different_ip_different_result(self):
        book = Logbook(salt="s1")
        self.assertNotEqual(book.player("1.2.3.4"), book.player("5.6.7.8"))

    def test_matches_sha256_prefix(self):
        expected = hashlib.sha256("盐|10.0.0.1".encode()).hexdigest()[:8]
        self.assertEqual(Logbook(salt="盐").player("10.0.0.1"), expected)
        expected_nosalt = hashlib.sha256("|10.0.0.1".encode()).hexdigest()[:8]
        self.assertEqual(Logbook().player("10.0.0.1"), expected_nosalt)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def read_raw(self, book):
        with open(book.path, encoding="utf-8") as f:
            return f.read()

    def test_lines_are_json_with_fields(self):
        book = Logbook(log_dir=self.dir, echo=False)
        book.write("say", cid="c1", votes=7)
        book.write("verdict", cid="c1", win=True)
        raw = self.read_raw(book)
        self.assertTrue(raw.endswith("\n"))
        lines = raw.split("\n")
        self.assertEqual(lines[-1], "")
        lines = lines[:-1]
        self.assertEqual(len(lines), 2)
        first, second = (json.loads(x) for x in lines)
        for entry in (first, second):
            self.assertIn("ts", entry)
            self.assertIn("t", entry)
            self.assertIsInstance(entry["ts"], float)
            self.assertRegex(entry["t"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(first["event"], "say")
        self.assertEqual(first["cid"], "c1")
        self.assertEqual(first["votes"], 7)
        self.assertEqual(second["event"], "verdict")
        self.assertIs(second["win"], True)

    def test_chinese_written_verbatim(self):
        book = Logbook(log_dir=self.dir, echo=False)
        book.write("say", text="他女儿才十岁")
        raw = self.read_raw(book)
        self.assertIn("他女儿才十岁", raw)
        self.assertNotIn("\\u", raw)
        self.assertEqual(json.loads(raw)["text"], "他女儿才十岁")

    def test_echo_prints_same_line(self):
        book = Logbook(log_dir=self.dir, echo=True)
        out = io.StringIO()
        with redirect_stdout(out):
            book.write("begin", cid="c2", mode="easy")
        printed = out.getvalue()
        self.assertTrue(printed.startswith("LOG "))
        self.assertTrue(printed.endswith("\n"))
        self.assertEqual(printed.count("\n"), 1)
        file_line = self.read_raw(book).rstrip("\n")
        self.assertEqual(printed[len("LOG "):].rstrip("\n"), file_line)
        self.assertEqual(json.loads(printed[4:])["mode"], "easy")

    def test_preview_not_printed_but_written(self):
        book = Logbook(log_dir=self.dir, echo=True)
        out = io.StringIO()
        with redirect_stdout(out):
            book.write("preview", draft="张伟")
        self.assertEqual(out.getvalue(), "")
        entry = json.loads(self.read_raw(book))
        self.assertEqual(entry["event"], "preview")
        self.assertEqual(entry["draft"], "张伟")

    def test_echo_false_prints_nothing(self):
        book = Logbook(log_dir=self.dir, echo=False)
        out = io.StringIO()
        with redirect_stdout(out):
            book.write("say", cid="c3")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(self.read_raw(book))["cid"], "c3")

    def test_no_log_dir_writes_nothing(self):
        book = Logbook(echo=False)
        cwd = os.getcwd()
        try:
            os.chdir(self.dir)
            out = io.StringIO()
            with redirect_stdout(out):
                book.write("say", cid="c4")
            self.assertEqual(os.listdir(self.dir), [])
            self.assertEqual(out.getvalue(), "")
        finally:
            os.chdir(cwd)

    def test_no_log_dir_still_echoes(self):
        book = Logbook(echo=True)
        out = io.StringIO()
        with redirect_stdout(out):
            book.write("open", cid="c5")
        printed = out.getvalue()
        self.assertTrue(printed.startswith("LOG "))
        self.assertEqual(json.loads(printed[4:])["cid"], "c5")


class PctTest(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(pct([], 50))
        self.assertIsNone(pct([None, None], 50))

    def test_ignores_none(self):
        self.assertEqual(pct([None, 1, None, 3], 50), 2)

    def test_single(self):
        self.assertEqual(pct([7], 0), 7)
        self.assertEqual(pct([7], 50), 7)
        self.assertEqual(pct([7], 100), 7)

    def test_median_interpolation(self):
        self.assertEqual(pct([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(pct([4, 1, 3, 2], 50), 2.5)  # 先排序
        self.assertEqual(pct([1, 2, 3], 50), 2)
        self.assertAlmostEqual(pct([10, 20, 30, 40, 50], 90), 46.0)

    def test_extremes(self):
        self.assertEqual(pct([5, 1, 9, 3], 0), 1)
        self.assertEqual(pct([5, 1, 9, 3], 100), 9)


class LatencyTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(latency([]), {})
        self.assertEqual(latency([None]), {})

    def test_values(self):
        # p50 = 300；p90：k = 3.6 → 400 + 600 × 0.6 = 760
        self.assertEqual(latency([100, 200, 300, 400, 1000]),
                         {"n": 5, "p50": 300, "p90": 760, "max": 1000})

    def test_rounding_and_none(self):
        # p50 = 150.4 → 150；p90 = 100.4 + 100 × 0.9 = 190.4 → 190；max 200.4 → 200
        self.assertEqual(latency([None, 100.4, None, 200.4]),
                         {"n": 2, "p50": 150, "p90": 190, "max": 200})


if __name__ == "__main__":
    unittest.main()
