import json
import os
import tempfile
import unittest

from twelve.stats import load, report


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


if __name__ == "__main__":
    unittest.main()
