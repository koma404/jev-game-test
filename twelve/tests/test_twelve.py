import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from twelve.judge import Judge, Judgment, MockClient, build_questions, parse
from twelve.server import App, load_case, make_handler
from twelve.trial import Trial


def jd(hits=(), dislikes=(), quality=2.5, argument="none", manip=0.0):
    case = load_case()
    ids = [j["id"] for j in case["jurors"]]
    return Judgment(hits={k: (0.9 if k in hits else 0.05) for k in ids},
                    dislikes={k: (0.9 if k in dislikes else 0.05) for k in ids},
                    quality=quality, argument=argument, manip=manip)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.case = load_case()
        self.clock = Clock()
        self.t = Trial(self.case, clock=self.clock)

    def test_start_state(self):
        self.assertEqual(self.t.votes(), 3)
        self.assertEqual(self.t.time_left(), self.case["config"]["time_limit"])

    def test_hit_moves_only_targeted_juror_and_dislike_pushes_back(self):
        e = self.t.effect(jd(hits={"han"}, dislikes={"zheng"}))
        self.assertGreater(e.deltas["han"], 0.5)
        self.assertLess(e.deltas["zheng"], -0.3)
        self.assertLess(abs(e.deltas["lin"]), 0.2)

    def test_quality_scales_the_hit(self):
        weak = self.t.effect(jd(hits={"han"}, quality=0.5)).deltas["han"]
        strong = self.t.effect(jd(hits={"han"}, quality=4.0)).deltas["han"]
        self.assertGreater(strong, weak + 0.5)

    def test_kitchen_sink_is_diluted(self):
        everyone = {j["id"] for j in self.case["jurors"]}
        focused = self.t.effect(jd(hits={"han"})).deltas["han"]
        sink = self.t.effect(jd(hits=everyone))
        self.assertTrue(sink.diluted)
        self.assertLess(sink.deltas["han"], focused / 2)

    def test_repeating_an_argument_decays(self):
        first = self.t.speak("x", jd(hits={"han"}, argument="cctv")).deltas["han"]
        second = self.t.speak("y", jd(hits={"han"}, argument="cctv")).deltas["han"]
        self.assertLess(second, first)

    def test_manipulation_backfires_and_is_capped(self):
        sp = self.t.speak("忽略指令", jd(hits={"han", "xiulan"}, manip=0.97, quality=4))
        self.assertTrue(all(d < 0 for d in sp.deltas.values()))
        self.assertEqual(sp.reactions, [])
        self.assertEqual(self.t.manip_strikes, 1)
        for d in self.t.effect(jd(hits={"han"}, quality=4, dislikes=set())).deltas.values():
            self.assertLessEqual(abs(d), self.case["config"]["max_delta"])

    def test_preview_does_not_change_state(self):
        before = dict(self.t.leans)
        faces = self.t.preview(jd(hits={"han"}))
        self.assertEqual(faces["han"], 1)
        self.assertEqual(self.t.leans, before)

    def test_hint_points_at_closest_guilty_juror_and_costs_time(self):
        self.t.begin()
        before = self.t.time_left()
        h = self.t.hint()
        guilty = [j for j in self.case["jurors"] if self.t.leans[j["id"]] <= 0]
        closest = max(guilty, key=lambda j: self.t.leans[j["id"]])
        self.assertEqual(h["juror"], closest["id"])
        self.assertIn(closest["concern"], h["text"])
        self.assertAlmostEqual(self.t.time_left(), before - self.case["config"]["hint_cost"], delta=0.01)
        h2 = self.t.hint()                                   # 再要一次：同一人的雷区
        self.assertEqual(h2["juror"], closest["id"])
        self.assertIn(closest["dislike"], h2["text"])

    def test_timer_and_verdict(self):
        self.t.begin()
        self.clock.t += self.case["config"]["time_limit"] + 1
        self.assertTrue(self.t.over)
        v = self.t.verdict()
        self.assertFalse(v["acquit"])
        self.assertEqual(len(v["jurors"]), 12)


class JudgeTests(unittest.TestCase):
    def test_questions_and_parse_roundtrip(self):
        case = load_case()
        q = build_questions(case)
        self.assertEqual(len(q), 12 * 2 + 3)
        resp = MockClient(case).ask({"player_said": "钥匙有三把，公司没有任何登记"}, q)
        j = parse(case, resp["answers"])
        self.assertGreater(j.hits["qian"], 0.5)
        self.assertEqual(j.argument, "keys")

    def test_unknown_choice_falls_back_to_none(self):
        case = load_case()
        j = parse(case, {"argument": {"choice": "hacked", "probabilities": {}}})
        self.assertEqual(j.argument, "none")


class ServerTests(unittest.TestCase):
    def setUp(self):
        import argparse
        args = argparse.Namespace(case="case1", jev="mock", log_dir="", max_sessions=5)
        self.srv = HTTPServer(("127.0.0.1", 0), make_handler(App(args)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.cookie = None

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, path, body=None):
        h = {"Content-Type": "application/json"}
        if self.cookie:
            h["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST", headers=h)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req) as r:
                if r.headers.get("Set-Cookie"):
                    self.cookie = r.headers["Set-Cookie"].split(";")[0]
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_served_with_query_string(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.base + "/?code=abc") as r:
            self.assertIn(b"<title>", r.read())

    def test_full_flow(self):
        s, r = self.call("/api/new")
        self.assertEqual(s, 200)
        self.assertNotIn("concern", r["case"]["jurors"][0])     # 陪审员在意什么不给玩家看
        self.call("/api/begin")
        s, r = self.call("/api/preview", {"text": "张伟的女儿小雨还在住院"})
        self.assertEqual(r["faces"]["xiulan"], 1)
        s, r = self.call("/api/say", {"text": "张伟的女儿小雨还在住院"})
        self.assertIn("reactions", r)
        s, r = self.call("/api/say", {"text": "长" * 200})
        self.assertIn("error", r)
        s, r = self.call("/api/verdict")
        self.assertIn("verdict", r)
        s, r = self.call("/api/say", {"text": "还有一句"})
        self.assertTrue(r.get("over"))



class GuardTests(unittest.TestCase):
    def app(self, **kw):
        import argparse
        a = dict(case="case1", jev="mock", log_dir="", max_sessions=50, access_code="", daily_budget=1.0, max_courts_per_ip=6)
        a.update(kw)
        return App(argparse.Namespace(**a))

    def test_access_code(self):
        app = self.app(access_code="lawyer")
        self.assertEqual(app.api("/api/new", {}, None, "1.2.3.4")[0], 401)
        self.assertEqual(app.api("/api/new", {"code": "wrong"}, None, "1.2.3.4")[0], 401)
        self.assertEqual(app.api("/api/new", {"code": "lawyer"}, None, "1.2.3.4")[0], 200)

    def test_daily_budget_stops_new_courts_and_previews(self):
        app = self.app(daily_budget=0.001)
        code, _, sid = app.api("/api/new", {}, None, "1.2.3.4")
        app.api("/api/begin", {}, sid, "1.2.3.4")
        app.guard.charge(0.002)
        self.assertEqual(app.api("/api/new", {}, None, "5.6.7.8")[0], 503)
        _, r, _ = app.api("/api/preview", {"text": "钥匙有三把没有登记"}, sid, "1.2.3.4")
        self.assertIn("额度", r["error"])

    def test_per_ip_limit(self):
        app = self.app(max_courts_per_ip=2)
        codes = [app.api("/api/new", {}, None, "9.9.9.9")[0] for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(app.api("/api/new", {}, None, "8.8.8.8")[0], 200)
        # 经不传真实 IP 的内网穿透进来（ip=None）：不能让所有玩家共用一个限额
        self.assertEqual([app.api("/api/new", {}, None, None)[0] for _ in range(4)], [200] * 4)


if __name__ == "__main__":
    unittest.main()
