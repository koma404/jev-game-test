import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from twelve.judge import Judge, Judgment, MockClient, build_questions, parse
from twelve.server import App, load_case, make_handler
from twelve.trial import Trial


def jd(hits=(), dislikes=(), quality=2.5, argument="none", manip=0.0, recite=0.0, incomplete=0.0):
    case = load_case()
    ids = [j["id"] for j in case["jurors"]]
    return Judgment(hits={k: (0.9 if k in hits else 0.05) for k in ids},
                    dislikes={k: (0.9 if k in dislikes else 0.05) for k in ids},
                    quality=quality, argument=argument, manip=manip, recite=recite, incomplete=incomplete)


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

    def test_harping_on_one_argument_backfires(self):
        # 同一句话讲十几遍不能把人磨成无罪：累计有上限，讲多了反而扣分
        total = 0.0
        notes = []
        for i in range(15):
            sp = self.t.speak("钥匙", jd(hits={"qian"}, argument="keys", quality=3))
            total += sp.deltas["qian"]
            notes += sp.notes
        first = self.t.history[0]["deltas"]["qian"]
        self.assertLess(total, first * 1.5)
        self.assertLess(self.t.history[-1]["deltas"]["qian"], 0)
        self.assertLess(self.t.history[-1]["deltas"]["han"], 0)          # 没被戳中的人也听烦了
        self.assertIn(self.case["texts"]["annoyed"], notes)

    def test_half_sentence_previews_but_does_not_count(self):
        # 打到半句时脸照样会动（读空气），但半句话说出口不算数、也不算用掉这个论点
        half = jd(hits={"han", "he"}, argument="no_loot", incomplete=0.95)
        self.assertEqual(self.t.preview(half)["han"], 1)
        before = dict(self.t.leans)
        sp = self.t.speak("失窃的电缆至今没有找到，不能", half)
        self.assertLess(self.t.leans["han"] - before["han"], 0.3)
        self.assertIn(self.case["texts"]["cut_off"], sp.notes)
        self.assertNotIn("no_loot", self.t.arg_uses)
        full = self.t.speak("…不能就这样定罪。", jd(hits={"han", "he"}, argument="no_loot")).deltas["han"]
        self.assertGreater(full, 0.5)

    def test_recite_ramp_spares_uncertain_lines(self):
        # 配置了 recite_from / recite_full 时，Jev 拿不准的（P 在门槛以下）不罚，明确照抄的罚满
        self.t.cfg = dict(self.t.cfg, recite_from=0.6, recite_full=0.9)
        clean = self.t.effect(jd(hits={"han"}, recite=0.05)).deltas["han"]
        unsure = self.t.effect(jd(hits={"han"}, recite=0.45)).deltas["han"]
        copied = self.t.effect(jd(hits={"han"}, recite=0.95)).deltas["han"]
        self.assertAlmostEqual(unsure, clean)
        self.assertLess(copied, clean * 0.5)

    def test_quality_center_shifts_the_shared_effect(self):
        self.t.cfg = dict(self.t.cfg, quality_center=1.5)
        self.assertGreater(self.t.effect(jd(quality=1.8)).deltas["lin"], 0)

    def test_reciting_the_dossier_moves_less_than_arguing_from_it(self):
        argued = self.t.effect(jd(hits={"han"}, recite=0.08)).deltas["han"]
        recited = self.t.effect(jd(hits={"han"}, recite=0.96))
        self.assertTrue(recited.recited)
        self.assertLess(recited.deltas["han"], argued * 0.5)
        sp = self.t.speak("念案卷", jd(hits={"han"}, recite=0.96))
        self.assertIn(self.case["texts"]["recited"], sp.notes)

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

    def test_free_hint_costs_no_time_and_stall_counts_flat_sentences(self):
        self.t.begin()
        before = self.t.time_left()
        self.t.hint(free=True)
        self.assertAlmostEqual(self.t.time_left(), before, delta=0.01)
        self.t.speak("a", jd(quality=1.0))
        self.t.speak("b", jd(quality=1.0))
        self.assertEqual(self.t.stall, 2)
        self.t.speak("c", jd(hits={"lin", "song", "xiulan"}, quality=3))
        self.assertEqual(self.t.stall, 0)

    def test_verdict_tells_who_was_won_over_by_which_line(self):
        self.t.speak("没感觉的一句", jd(quality=1.5))
        self.t.speak("打动秀兰林护士的一句", jd(hits={"xiulan", "lin", "song"}, quality=3))
        v = self.t.verdict()
        by = {j["id"]: j["by"] for j in v["jurors"]}
        self.assertEqual(by["xiulan"], "打动秀兰林护士的一句")
        self.assertIsNone(by["ajie"])                      # 一开始就倾向无罪，不是被哪句话翻过来的
        self.assertEqual(v["best"]["text"], "打动秀兰林护士的一句")
        self.assertIn("秀兰", v["best"]["moved"])

    def test_hard_juror_flipping_sways_the_undecided_once(self):
        for j in self.t.jurors:
            if j["id"] == "lin":
                j["influence"] = 0.4
        self.t.leans["lin"] = -0.1
        before = dict(self.t.leans)
        sp = self.t.speak("打动林护士", jd(hits={"lin"}, quality=1.5))
        self.assertEqual(sp.cascade[0]["juror"], "lin")
        self.assertIn("han", sp.cascade[0]["moved"])
        self.assertNotIn("ajie", sp.cascade[0]["moved"])            # 已经倾向无罪的人不算
        self.assertGreater(self.t.leans["han"] - before["han"], 0.35)
        self.t.leans["lin"] = -0.5
        sp2 = self.t.speak("再打动一次", jd(hits={"lin"}, quality=1.5, argument="x"))
        self.assertEqual(sp2.cascade, [])                             # 每人只触发一次

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
        self.assertEqual(len(q), 12 * 2 + 5)
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
        args = argparse.Namespace(case="case1", jev="mock", log_dir="", max_sessions=5, log_echo=False)
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

    def test_client_ip_from_proxy_headers(self):
        import os
        def ip_for(headers, peer):
            h = make_handler(App(__import__("argparse").Namespace(case="case1", jev="mock", log_dir="", max_sessions=5)))
            o = object.__new__(h)
            o.client_address = (peer, 1)
            o.headers = headers
            return o._ip()
        self.assertEqual(ip_for({"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}, "127.0.0.1"), "1.1.1.1")
        self.assertEqual(ip_for({"CF-Connecting-IP": "2.2.2.2"}, "127.0.0.1"), "2.2.2.2")
        self.assertEqual(ip_for({"X-Forwarded-For": "1.1.1.1"}, "8.8.8.8"), "8.8.8.8")   # 外部直连不信代理头
        os.environ["TWELVE_TRUST_PROXY"] = "1"
        try:
            self.assertEqual(ip_for({"X-Forwarded-For": "3.3.3.3"}, "10.1.2.3"), "3.3.3.3")
        finally:
            os.environ.pop("TWELVE_TRUST_PROXY")

    def test_page_served_with_query_string(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.base + "/?code=abc") as r:
            self.assertIn(b"<title>", r.read())

    def test_auto_hint_when_stuck(self):
        self.call("/api/new")
        self.call("/api/begin")
        r1 = self.call("/api/say", {"text": "今天天气不错啊各位"})[1]
        r2 = self.call("/api/say", {"text": "我们来聊聊别的事情吧"})[1]
        self.assertIsNone(r1["auto_hint"])
        self.assertTrue(r2["auto_hint"]["text"])                  # 连着两句没涨票：送一次提示

    def test_full_flow(self):
        s, r = self.call("/api/new")
        self.assertEqual(s, 200)
        self.assertNotIn("concern", r["case"]["jurors"][0])     # 陪审员在意什么不给玩家看
        self.call("/api/begin")
        s, r = self.call("/api/preview", {"text": "张伟的女儿小雨还在住院，他怎么会去冒这个险"})
        self.assertEqual(r["faces"]["xiulan"], 1)
        s, r = self.call("/api/say", {"text": "张伟的女儿小雨还在住院，他怎么会去冒这个险"})
        self.assertIn("reactions", r)
        s, r = self.call("/api/say", {"text": "长" * 200})
        self.assertIn("error", r)
        s, r = self.call("/api/verdict")
        self.assertIn("verdict", r)
        s, r = self.call("/api/say", {"text": "还有一句"})
        self.assertTrue(r.get("over"))



class LogTests(unittest.TestCase):
    """运行日志：每局的事件都记下来，/admin 能看统计、下载日志，没有管理口令时打不开。"""

    def setUp(self):
        import argparse, tempfile
        self.dir = tempfile.mkdtemp()
        args = argparse.Namespace(case="case1", jev="mock", log_dir=self.dir, max_sessions=5,
                                  admin_code="adm", log_echo=False)
        self.srv = HTTPServer(("127.0.0.1", 0), make_handler(App(args)))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.cookie = None

    def tearDown(self):
        import shutil
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, path, body=None):
        h = {"Content-Type": "application/json", "User-Agent": "TestBrowser/1.0"}
        if self.cookie:
            h["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST", headers=h)
        with self.opener.open(req) as r:
            if r.headers.get("Set-Cookie"):
                self.cookie = r.headers["Set-Cookie"].split(";")[0]
            return json.loads(r.read())

    def get(self, path):
        try:
            with self.opener.open(self.base + path) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, ""

    def events(self):
        from twelve.stats import load
        import os
        return load([os.path.join(self.dir, "twelve.jsonl")])

    def test_a_game_is_logged_end_to_end(self):
        import time
        self.call("/api/new")
        self.call("/api/begin")
        self.call("/api/preview", {"text": "张伟的女儿小雨还在住院，他怎么会去冒这个险", "prev": 0})
        time.sleep(0.2)
        self.call("/api/preview", {"text": "张伟的女儿小雨还在住院，他是个父亲", "prev": 640})
        self.call("/api/say", {"text": "张伟的女儿小雨还在住院，他是个父亲"})
        self.call("/api/hint")
        self.call("/api/verdict")
        self.call("/api/verdict")                       # 重复请求裁决只记一次
        ev = self.events()
        kinds = [e["event"] for e in ev]
        for k in ("open", "begin", "preview", "say", "hint", "verdict"):
            self.assertIn(k, kinds)
        self.assertEqual(kinds.count("verdict"), 1)
        self.assertEqual(len({e["cid"] for e in ev}), 1)
        op = ev[kinds.index("open")]
        self.assertEqual(op["ua"], "TestBrowser/1.0")
        say = ev[kinds.index("say")]
        self.assertTrue(say["cached"])                  # 预览过的原句直接说出口，复用判断
        self.assertIn("server_ms", say)
        v = ev[kinds.index("verdict")]
        self.assertEqual((v["says"], v["previews"], v["hints"], v["reason"]), (1, 2, 1, "rested"))
        self.assertEqual(v["latency"]["browser"]["p50"], 640)
        self.assertNotIn(self.cookie.split("=", 1)[1], json.dumps(ev))   # cookie 不进日志

    def test_timeout_during_say_is_logged_as_verdict(self):
        self.call("/api/new")
        self.call("/api/begin")
        app_court = None
        # 直接把这一庭的时钟拨到结束
        for c in self.srv.RequestHandlerClass.app.courts.values():
            app_court = c
        app_court.trial.started_at -= 10_000
        r = self.call("/api/say", {"text": "他在公司守了八年夜"})
        self.assertTrue(r.get("over"))
        v = [e for e in self.events() if e["event"] == "verdict"]
        self.assertEqual(v[0]["reason"], "timeout")

    def test_admin_code_opens_the_beta_court(self):
        app = self.srv.RequestHandlerClass.app
        app.guard.code = "player"
        r = self.call("/api/new", {"code": "adm"})
        self.assertEqual(r["channel"], "beta")
        self.cookie = None
        r = self.call("/api/new", {"code": "player"})
        self.assertEqual(r["channel"], "release")
        chans = [e.get("channel") for e in self.events() if e["event"] == "open"]
        self.assertEqual(chans, ["beta", "release"])

    def test_wrong_code_is_logged_as_refused(self):
        self.srv.RequestHandlerClass.app.guard.code = "abc"
        try:
            self.call("/api/new", {"code": "nope"})
        except urllib.error.HTTPError:
            pass
        self.assertEqual([e["reason"] for e in self.events() if e["event"] == "refused"], ["code"])

    def test_admin_needs_key(self):
        self.assertEqual(self.get("/admin")[0], 404)
        self.assertEqual(self.get("/admin?key=wrong")[0], 404)
        self.assertEqual(self.get("/admin/log?key=wrong")[0], 404)
        self.call("/api/new")
        self.call("/api/begin")
        self.call("/api/say", {"text": "凌晨的监控里那个人的脸看不清"})
        self.call("/api/verdict")
        s, body = self.get("/admin?key=adm")
        self.assertEqual(s, 200)
        self.assertIn("胜负", body)
        self.assertIn("陪审员", body)
        s, body = self.get("/admin/log?key=adm")
        self.assertIn('"event": "verdict"', body)

    def test_stats_reads_copied_platform_logs(self):
        import os, tempfile
        from twelve.stats import load, report
        line = json.dumps({"ts": 1.0, "t": "2026-10-16 10:00:00", "event": "verdict", "cid": "x", "acquit": True,
                           "votes": 8, "leans": {"han": 0.5}, "says": 5, "previews": 80, "hints": 1,
                           "reason": "rested", "latency": {"jev": {"n": 3, "p50": 800, "p90": 900, "max": 950}}},
                          ensure_ascii=False)
        p = os.path.join(self.dir, "render.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("Oct 16 10:00:00 AM  LOG " + line + "\n")
            f.write("==> Your service is live\n")
            f.write("Oct 16 10:00:00 AM  LOG " + line + "\n")   # 重复的行去重
        ev = load([p])
        self.assertEqual(len(ev), 1)
        text = report(ev)
        self.assertIn("胜诉 1/1", text)
        self.assertIn("800", text)


class LevelTests(unittest.TestCase):
    """关卡框架：选关、只在内测里出现的新关卡、庭审中途的事件。"""

    def setUp(self):
        import argparse, shutil, tempfile
        from twelve.server import HERE
        self.dir = tempfile.mkdtemp()
        shutil.copy(HERE / "cases" / "case1.json", self.dir)
        two = load_case()
        two.update({"id": "case9", "title": "测试关", "order": 9, "stars": 2, "beta_only": True, "blurb": "测试用"})
        two["events"] = [
            {"id": "new_evidence", "after_says": 1, "title": "检方出示新证据", "text": "一张新的监控截图。",
             "dossier": ["检方：新截图里那人左手戴着表。"], "leans": {"han": -1.0}, "leans_all": -0.2, "time": 15,
             "jurors": [{"id": "han", "concern": "被告是不是左撇子、戴不戴表"}]},
            {"id": "late", "at_time_left": 100000, "title": "休庭前", "text": "法官看了看表。"}]
        with open(f"{self.dir}/case9.json", "w", encoding="utf-8") as f:
            json.dump(two, f, ensure_ascii=False)
        args = argparse.Namespace(case="all", cases_dir=self.dir, jev="mock", log_dir="", max_sessions=9,
                                  admin_code="adm", log_echo=False)
        self.app = App(args)
        self.app.guard.code = "player"
        self.srv = HTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.cookie = None

    def tearDown(self):
        import shutil
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, path, body=None):
        h = {"Content-Type": "application/json"}
        if self.cookie:
            h["Cookie"] = self.cookie
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST", headers=h)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as r:
            if r.headers.get("Set-Cookie"):
                self.cookie = r.headers["Set-Cookie"].split(";")[0]
            return json.loads(r.read())

    def test_players_see_only_released_cases(self):
        r = self.call("/api/new", {"code": "player"})      # 只有一个能玩的：直接进
        self.assertEqual(r["case"]["id"], "case1")
        self.cookie = None
        r = self.call("/api/new", {"code": "player", "case": "case9"})   # 想绕过去也不行
        self.assertEqual(r["case"]["id"], "case1")

    def test_admin_picks_a_level(self):
        r = self.call("/api/new", {"code": "adm"})
        self.assertEqual([c["id"] for c in r["cases"]], ["case1", "case9"])
        self.assertNotIn("case", r)
        r = self.call("/api/new", {"code": "adm", "case": "case9"})
        self.assertEqual((r["case"]["id"], r["channel"]), ("case9", "beta"))

    def test_event_fires_once_and_changes_the_trial(self):
        self.call("/api/new", {"code": "adm", "case": "case9"})
        self.call("/api/begin")
        court = next(iter(self.app.courts.values()))
        court.case["events"] = court.case["events"][:1]       # 只测"说够一句"这个事件
        han0 = court.trial.leans["han"]
        left0 = court.trial.time_left()
        r = self.call("/api/say", {"text": "今天天气不错啊各位"})
        self.assertEqual([e["id"] for e in r["events"]], ["new_evidence"])
        self.assertIn("检方：新截图里那人左手戴着表。", r["dossier"])
        self.assertLess(r["leans"]["han"], han0 - 1.0)
        self.assertGreater(court.trial.time_left(), left0)               # 补了读证据的时间
        self.assertIn("左撇子", court.judge.questions["hit_han"]["instructions"]["concern"])   # 题目跟着变
        from twelve.judge import state_for
        self.assertIn("左手戴着表", state_for(court.judge.case, "x")["case"])          # 新证据进了发给 Jev 的 state
        r = self.call("/api/say", {"text": "我们来聊聊别的事情吧"})
        self.assertNotIn("events", r)
        other = self.app.cases["case9"]["beta"]
        self.assertEqual(len(other["dossier"]), len(load_case()["dossier"]))   # 不影响别的庭

    def test_time_event_can_fire_while_typing(self):
        self.call("/api/new", {"code": "adm", "case": "case9"})
        self.call("/api/begin")
        court = next(iter(self.app.courts.values()))
        court.case["events"] = court.case["events"][1:]
        r = self.call("/api/preview", {"text": "张伟"})
        self.assertEqual([e["id"] for e in r["events"]], ["late"])


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

    def test_unknown_ip_is_not_rate_limited_as_one_player(self):
        """内网穿透不带真实 IP 时，所有玩家都像是 127.0.0.1；不能让他们共用一个开庭额度。"""
        app = self.app(max_courts_per_ip=2)
        codes = [app.api("/api/new", {}, None, None)[0] for _ in range(5)]
        self.assertEqual(codes, [200] * 5)
        # 经不传真实 IP 的内网穿透进来（ip=None）：不能让所有玩家共用一个限额
        self.assertEqual([app.api("/api/new", {}, None, None)[0] for _ in range(4)], [200] * 4)


if __name__ == "__main__":
    unittest.main()
