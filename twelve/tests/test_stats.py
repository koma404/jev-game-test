import contextlib
import io
import json
import os
import tempfile
import unittest

from twelve.stats import filter_events, load, main, report


def verdict(**kw):
    e = {"ts": 1.0, "t": "2026-01-01 00:00:00", "event": "verdict", "cid": "c1", "acquit": False,
         "votes": 3, "case": "case1", "mode": "normal", "reason": "rested"}
    e.update(kw)
    return e


class LoadTest(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_plain_jsonl(self):
        events = [{"ts": 1, "t": "a", "event": "open", "player": "p1"},
                  {"ts": 2, "t": "b", "event": "begin", "cid": "c1"}]
        path = self.write("\n".join(json.dumps(e) for e in events) + "\n")
        got = load([path])
        self.assertEqual([e["event"] for e in got], ["open", "begin"])
        self.assertEqual(got[1]["cid"], "c1")

    def test_log_prefix_and_noise(self):
        lines = [
            "2026-01-01T00:00:00 some platform noise without json",
            'LOG {"ts": 2, "t": "b", "event": "verdict", "cid": "c1"}',
            "  at handler (server.js:1:1)",
            'INFO  LOG {"ts": 1, "t": "a", "event": "open", "player": "p1"}',
            "not json {broken",
        ]
        path = self.write("\n".join(lines) + "\n")
        got = load([path])
        self.assertEqual([e["event"] for e in got], ["open", "verdict"])
        self.assertEqual([e["ts"] for e in got], [1, 2])

    def test_dedup_across_files(self):
        text = '{"ts": 1, "t": "a", "event": "open", "cid": "c1"}\n'
        a, b = self.write(text), self.write("LOG " + text)
        self.assertEqual(len(load([a, b])), 1)

    def test_sorted_by_ts(self):
        a = self.write('{"ts": 5, "event": "begin"}\n{"ts": 3, "event": "open"}\n')
        self.assertEqual([e["ts"] for e in load([a])], [3, 5])


class ReportTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(report([]), "还没有日志。")

    def events(self):
        t = "2026-01-01 00:00:00"
        return [
            {"ts": 1, "t": t, "event": "open", "player": "p1"},
            {"ts": 2, "t": t, "event": "open", "player": "p2"},
            {"ts": 3, "t": t, "event": "begin", "cid": "c1"},
            {"ts": 4, "t": t, "event": "say", "cid": "c1", "votes": 5, "text": "他说谎",
             "argument": "motive", "quality": 2.0, "manip": 0.0,
             "leans": {"j1": 0.2, "j2": -0.1}, "hits": {"j1": 0.9}, "dislikes": {}},
            verdict(ts=5, cid="c1", acquit=True, votes=5, says=4, previews=2, hints=1, used_s=60, cost=0.02),
            verdict(ts=6, cid="c2", acquit=False, votes=3, says=2, previews=0, hints=0, used_s=30, cost=0.01),
            verdict(ts=7, cid="c3", acquit=True, votes=4),
        ]

    def test_counts(self):
        text = report(self.events())
        self.assertIn("开庭 2 次（2 个不同 IP）", text)
        self.assertIn("开始陈词 1", text)
        self.assertIn("完成裁决 3", text)
        self.assertIn("胜诉 2/3（67%）", text)
        self.assertIn("5票×1", text)
        self.assertIn("3票×1", text)
        self.assertIn("主动结束 3", text)
        self.assertIn("说出口 3.0 句", text)
        self.assertIn("他说谎", text)

    def test_easy_mode(self):
        text = report([verdict(mode="easy", acquit=True), verdict(mode="easy", acquit=False)])
        self.assertIn("精炼陈词 2 局 胜 1", text)

    def test_normal_mode_has_no_mode_line(self):
        self.assertNotIn("精炼陈词", report([verdict()]))

    def test_without_verdicts(self):
        text = report([{"ts": 1, "t": "a", "event": "open", "player": "p1"}])
        self.assertIn("还没有完成的对局。", text)
        self.assertIn("开庭 1 次", text)


class FilterTest(unittest.TestCase):
    def events(self):
        return [
            {"ts": 1, "t": "2026-10-16 23:59:59", "event": "open", "case": "case1"},
            {"ts": 2, "t": "2026-10-17 00:00:00", "event": "begin", "case": "case2"},
            {"ts": 3, "t": "2026-10-18 12:00:00", "event": "say"},
            {"ts": 4, "t": "2026-10-18 23:59:59", "event": "verdict", "case": "case2"},
            {"ts": 5, "t": "2026-10-19 00:00:00", "event": "verdict", "case": "case1"},
            {"ts": 6, "event": "client_error", "case": "case2"},
        ]

    def test_no_filters_returns_new_list(self):
        ev = self.events()
        got = filter_events(ev)
        self.assertEqual(got, ev)
        self.assertIsNot(got, ev)
        got.pop()
        self.assertEqual(len(ev), 6)

    def test_date_bounds_inclusive(self):
        got = filter_events(self.events(), since="2026-10-17", until="2026-10-18")
        self.assertEqual([e["ts"] for e in got], [2, 3, 4])

    def test_datetime_bounds_inclusive(self):
        got = filter_events(self.events(), since="2026-10-18 12:00:00", until="2026-10-19 00:00:00")
        self.assertEqual([e["ts"] for e in got], [3, 4, 5])
        self.assertEqual([e["ts"] for e in filter_events(self.events(), until="2026-10-16 23:59:58")], [])

    def test_missing_t_excluded_only_with_time_filter(self):
        self.assertNotIn(6, [e["ts"] for e in filter_events(self.events(), since="2026-01-01")])
        self.assertIn(6, [e["ts"] for e in filter_events(self.events(), case="case2")])

    def test_case_defaults_to_case1(self):
        self.assertEqual([e["ts"] for e in filter_events(self.events(), case="case1")], [1, 3, 5])
        got = filter_events(self.events(), since="2026-10-17", case="case2")
        self.assertEqual([e["ts"] for e in got], [2, 4])


class MainTest(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def path(self):
        events = [
            verdict(ts=1, t="2026-10-17 10:00:00", cid="a", case="case1", acquit=True, votes=7),
            verdict(ts=2, t="2026-10-18 10:00:00", cid="b", case="case2", acquit=False, votes=3),
            verdict(ts=3, t="2026-10-18 11:00:00", cid="c", case="case2", acquit=False, votes=2),
        ]
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(e) for e in events) + "\n")
        return path

    def test_plain_path_unchanged(self):
        code, text = self.run_main([self.path()])
        self.assertEqual(code, 0)
        self.assertIn("完成裁决 3", text)

    def test_options_anywhere(self):
        path = self.path()
        code, text = self.run_main(["--case", "case2", path, "--since", "2026-10-18 10:30:00"])
        self.assertEqual(code, 0)
        self.assertIn("完成裁决 1", text)
        code, text = self.run_main([path, "--until", "2026-10-17"])
        self.assertIn("完成裁决 1", text)
        self.assertIn("胜诉 1/1", text)

    def test_filter_to_nothing(self):
        code, text = self.run_main([self.path(), "--since", "2027-01-01"])
        self.assertEqual(code, 0)
        self.assertIn("还没有日志。", text)


if __name__ == "__main__":
    unittest.main()
