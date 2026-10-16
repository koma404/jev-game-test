import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from game import verify
from game.engine import Engine, TurnLogger
from game.flavor import FlavorWriter
from game.jev_client import JevClient, JevError, MockJev, RealJev
from game.tests.eval_intents import evaluate
from game.world import State, World


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def ids(self, st, code=None):
        return {r["id"] for r in self.w.legal_rules(st, code)}

    def test_initial_hall(self):
        ids = self.ids(self.w.initial())
        self.assertEqual(ids, {"go_archive", "examine_shutter", "talk_zhou", "look_around", "check_items", "ask_hint"})

    def test_drawer_needs_both_clues_and_code(self):
        st = State("archive")
        self.assertNotIn("open_drawer", self.ids(st, "0417"))
        st = State("archive", frozenset({"zhou_open", "lin_open"}))
        self.assertNotIn("open_drawer", self.ids(st))
        self.assertIn("open_drawer", self.ids(st, "0417"))
        self.assertNotIn("wrong_code", self.ids(st, "0417"))
        self.assertIn("wrong_code", self.ids(st, "1234"))

    def test_effects(self):
        st = State("archive", frozenset({"zhou_open", "lin_open"}))
        st = self.w.apply(st, self.w.rule_by_id["open_drawer"])
        self.assertIn("keycard", st.items)
        self.assertIn("drawer_open", st.flags)
        self.assertNotIn("open_drawer", self.ids(st, "0417"))

    def test_basement_locked_until_keycard_used(self):
        st = State("hall", items=frozenset({"keycard"}))
        self.assertNotIn("go_basement", self.ids(st))
        st = self.w.apply(st, self.w.rule_by_id["use_keycard"])
        self.assertIn("go_basement", self.ids(st))

    def test_code_regex_and_score_rounding(self):
        self.assertEqual(self.w.extract_code("输入 0417 试试"), "0417")
        self.assertIsNone(self.w.extract_code("12345"))
        self.assertEqual(self.w.score_level(1.05), 1)
        self.assertEqual(self.w.score_level(1.5), 2)
        self.assertEqual(self.w.score_level(9), 3)

    def test_trust_clamped(self):
        st = self.w.initial()
        for _ in range(20):
            st = self.w.adjust_trust(st, "zhou", 2)
        self.assertEqual(st.get_trust("zhou"), self.w.cfg["trust_max"])


class VerifyTests(unittest.TestCase):
    def test_level1(self):
        a = verify.analyze(World())
        self.assertTrue(a["solvable"])
        self.assertEqual(a["dead"], [])
        self.assertEqual(a["unreachable"], [])
        dep = {rid: crit for rid, _, crit in a["deps"]}
        self.assertEqual(set(dep["open_drawer"]), {"zhou_open", "lin_open"})

    def test_detects_unsolvable_and_unreachable(self):
        with open(os.path.join(os.path.dirname(verify.__file__), "levels", "level1.json"), encoding="utf-8") as f:
            lv = json.load(f)
        lv["rules"] = [r for r in lv["rules"] if r["id"] != "pull_switch"]
        a = verify.analyze(World(lv))
        self.assertFalse(a["solvable"])
        self.assertIn("exit", a["unreachable"])


class Scripted(JevClient):
    """按脚本返回固定概率，用来精确测门槛逻辑。"""
    name = "scripted"

    def __init__(self, **answers):
        self.answers = answers

    def ask(self, state, questions):
        out = {}
        for k, v in self.answers.items():
            if k in questions:
                out[k] = v
        return {"answers": out}


def choice(probs):
    top = max(probs, key=probs.get)
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": probs[top]}


def score(v):
    return {"type": "score", "score": v, "confidence": 0.9}


class EngineTests(unittest.TestCase):
    def test_full_playthrough_with_mock(self):
        e = Engine(World(), MockJev())
        script = ["我想跟守卫聊聊，顺便问问这里怎么停电了",
                  "我是来查老馆长留下的资料的，他是个很受人尊敬的人",
                  "去档案室",
                  "您好，麻烦问下，馆长那么受人尊敬，抽屉密码是他的生日吗",
                  "输入0417", "回大厅", "用门禁卡刷开机房", "下楼去机房", "拉总闸", "回大厅", "我要离开这里"]
        res = [e.turn(t) for t in script]
        self.assertTrue(res[-1].done, [r.text for r in res])
        self.assertEqual(res[0].info["reaction"], "zhou_deflect")
        self.assertIn("馆长生日: 0417", e.clues)

    def test_low_confidence_asks_back(self):
        e = Engine(World(), MockJev())
        r = e.turn("那个东西能打开吗")
        self.assertEqual(r.kind, "clarify")
        self.assertEqual(e.state, World().initial())

    def test_generic_refuse(self):
        e = Engine(World(), MockJev(), state=State("archive"))
        r = e.turn("我直接把抽屉砸开吧")
        self.assertEqual(r.kind, "refuse")
        self.assertIn("小林", r.text)

    def test_trust_gate_downgrades_reaction(self):
        jev = Scripted(intent=choice({"go_hall": .05, "talk_lin": .9, "none": .05}),
                       attitude=score(2.0),
                       reaction=choice({"lin_confirm": .52, "lin_probe": .41, "lin_refuse": .07}))
        e = Engine(World(), jev, state=State("archive"))
        r = e.turn("你好，我想问下抽屉的密码，是馆长的生日吧？")
        self.assertEqual(r.info["reaction"], "lin_probe")
        self.assertEqual(r.info["blocked"], ["lin_confirm"])
        self.assertNotIn("lin_open", e.state.flags)
        r = e.turn("你好，我想问下抽屉的密码，是馆长的生日吧？")   # 信任 1 -> 2，达标
        self.assertEqual(r.info["reaction"], "lin_confirm")
        self.assertIn("lin_open", e.state.flags)

    def test_choice_outside_legal_set_never_executes(self):
        jev = Scripted(intent=choice({"open_drawer": .95, "none": .05}))
        e = Engine(World(), jev)
        r = e.turn("开抽屉")
        self.assertEqual(r.kind, "clarify")   # 越界选项被丢弃，剩下的概率太低，反问
        self.assertEqual(e.state, World().initial())

    def test_exit_needs_noul_confirmation(self):
        st = State("hall", frozenset({"power_on"}))
        jev = Scripted(intent=choice({"exit": .9, "go_archive": .02, "examine_shutter": .02,
                                      "talk_zhou": .02, "none": .04}),
                       attitude=score(1.0), reaction=choice({"zhou_deflect": 1.0}),
                       confirm={"type": "noul", "noul": 0.2})
        e = Engine(World(), jev, state=st)
        self.assertEqual(e.turn("门口那边好像开了").kind, "confirm")
        self.assertFalse(e.world.is_won(e.state))
        jev.answers["confirm"] = {"type": "noul", "noul": 0.9}
        self.assertTrue(e.turn("对，我要走了").done)

    def test_only_needed_state_is_sent(self):
        seen = []
        e = Engine(World(), MockJev(), on_exchange=lambda req, resp: seen.append(req))
        e.turn("你好老周")
        self.assertEqual(set(seen[0]["state"]), {"room", "player_said", "npc"})

    def test_jev_failure_does_not_consume_turn(self):
        class Down(JevClient):
            def ask(self, s, q):
                raise JevError("boom")
        e = Engine(World(), Down())
        r = e.turn("你好")
        self.assertEqual(r.kind, "jev_down")
        self.assertEqual(e.turn_no, 0)

    def test_logs(self):
        with tempfile.TemporaryDirectory() as d:
            e = Engine(World(), MockJev(), logger=TurnLogger(d))
            e.turn("那个东西能打开吗")
            e.turn("你好老周")
            lines = open(os.path.join(d, "low_conf.jsonl"), encoding="utf-8").read().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(len(open(os.path.join(d, "turns.jsonl"), encoding="utf-8").read().splitlines()), 2)


class IntentTests(unittest.TestCase):
    def test_mock_accuracy(self):
        res = evaluate(MockJev())
        wrong = [(s["text"], s["expect"], g) for s, g in res if s["expect"] != g]
        self.assertGreaterEqual(len(res) - len(wrong), len(res) * 0.9, wrong)


class FakeJevHandler(BaseHTTPRequestHandler):
    calls = 0
    fail_first = 0

    def do_POST(self):
        cls = type(self)
        cls.calls += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls.last = (self.headers.get("Authorization"), body)
        if self.headers.get("Authorization") != "Bearer sk-test":
            return self._send(401, {"error": "bad key"})
        if cls.calls <= cls.fail_first:
            return self._send(529, {"error": "overloaded"})
        resp = MockJev().ask(body["state"], body["questions"])
        resp["model"] = "jev-fake"
        self._send(200, resp)

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class RealJevTests(unittest.TestCase):
    def setUp(self):
        FakeJevHandler.calls, FakeJevHandler.fail_first = 0, 0
        self.srv = HTTPServer(("127.0.0.1", 0), FakeJevHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1/systemone"
        self._proxy = {k: os.environ.pop(k) for k in list(os.environ) if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.environ.update(self._proxy)

    def jev(self, key="sk-test", **kw):
        return RealJev(api_key=key, base_url=self.url, sleep=lambda s: None, **kw)

    def test_request_format_and_engine_roundtrip(self):
        e = Engine(World(), self.jev())
        r = e.turn("你好老周，我是来查资料的")
        self.assertEqual(r.kind, "act")
        auth, body = FakeJevHandler.last
        self.assertEqual(auth, "Bearer sk-test")
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(set(body), {"state", "model", "questions"})
        self.assertEqual(body["questions"]["intent"]["type"], "choice")

    def test_retries_on_529(self):
        FakeJevHandler.fail_first = 2
        self.jev().ask({"player_said": "你好"}, {"intent": {"type": "choice", "criteria": {"a": None, "none": None}}})
        self.assertEqual(FakeJevHandler.calls, 3)

    def test_no_retry_on_401(self):
        with self.assertRaises(JevError):
            self.jev(key="wrong").ask({"player_said": "x"}, {})
        self.assertEqual(FakeJevHandler.calls, 1)

    def test_gives_up_after_max_retries(self):
        FakeJevHandler.fail_first = 99
        with self.assertRaises(JevError):
            self.jev(max_retries=2).ask({"player_said": "x"}, {})
        self.assertEqual(FakeJevHandler.calls, 3)

    def test_key_file_must_be_private(self):
        saved_key = os.environ.pop("TYPESAFE_API_KEY", None)
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("sk-test\n")
        try:
            os.chmod(f.name, 0o644)
            os.environ["TYPESAFE_KEY_FILE"] = f.name
            with self.assertRaises(JevError):
                RealJev(base_url=self.url)
            os.chmod(f.name, 0o600)
            self.assertEqual(RealJev(base_url=self.url)._key, "sk-test")
        finally:
            os.environ.pop("TYPESAFE_KEY_FILE", None)
            if saved_key is not None:
                os.environ["TYPESAFE_API_KEY"] = saved_key
            os.unlink(f.name)


class HintTests(unittest.TestCase):
    def test_hints_progress_and_reset_by_stage(self):
        e = Engine(World(), MockJev())
        first = [e.hint() for _ in range(4)]
        self.assertTrue(first[0].startswith("（提示 1/3）"))
        self.assertTrue(first[2].startswith("（提示 3/3）"))
        self.assertEqual(first[3][:8], first[2][:8])      # 到顶后重复最直白的一层
        e.state = State("hall", frozenset({"zhou_open"}))
        self.assertTrue(e.hint().startswith("（提示 1/3）"))  # 阶段变了从头开始

    def test_every_reachable_state_has_a_hint(self):
        w = World()
        for flags, items in [((), ()), (("zhou_open",), ()), (("lin_open",), ()),
                             (("zhou_open", "lin_open"), ()), (("zhou_open", "lin_open", "drawer_open"), ("keycard",)),
                             (("basement_open",), ("keycard",)), (("power_on",), ("keycard",))]:
            idx, levels = w.hint_stage(State("hall", frozenset(flags), frozenset(items)))
            self.assertGreaterEqual(idx, 0, (flags, items))
            self.assertEqual(len(levels), 3)


class GuidanceTests(unittest.TestCase):
    def test_elsewhere_gives_directions_without_changing_state(self):
        e = Engine(World(), MockJev(), state=State("archive"))
        before = e.state
        r = e.turn("找老周")
        self.assertEqual(r.kind, "elsewhere")
        self.assertIn("大厅", r.text)
        self.assertEqual(e.state, before)

    def test_elsewhere_only_offered_when_only_room_differs(self):
        w = World()
        ids = {r["id"] for r in w.elsewhere_rules(State("archive"))}
        self.assertIn("talk_zhou", ids)
        self.assertNotIn("use_keycard", ids)           # 还没有门禁卡
        ids = {r["id"] for r in w.elsewhere_rules(State("archive", items=frozenset({"keycard"})))}
        self.assertIn("use_keycard", ids)

    def test_npc_lines_rotate_instead_of_repeating(self):
        e = Engine(World(), MockJev())
        lines = [e.turn("老周，你好").text for _ in range(3)]
        self.assertEqual(len(set(lines)), 3)

    def test_spoilers_follow_progress(self):
        w = World()
        self.assertIn("门禁卡", w.active_spoilers(State("archive")))
        self.assertNotIn("门禁卡", w.active_spoilers(State("archive", items=frozenset({"keycard"}))))
        self.assertIn("0417", w.active_spoilers(State("hall")))
        self.assertNotIn("0417", w.active_spoilers(State("hall", frozenset({"zhou_open"}))))

    def test_item_names_and_hint_via_natural_language(self):
        w = World()
        self.assertEqual(w.item_name("keycard"), "蓝色门禁卡")
        e = Engine(w, MockJev())
        self.assertIn("提示", e.turn("给我提示").text)


class FlavorHandler(BaseHTTPRequestHandler):
    reply = "拳头很热情，可惜游戏不收。"
    status = 200
    last = None

    def do_POST(self):
        FlavorHandler.last = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps({"choices": [{"message": {"content": FlavorHandler.reply}}]}).encode()
        self.send_response(FlavorHandler.status)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class FlavorTests(unittest.TestCase):
    def setUp(self):
        FlavorHandler.reply, FlavorHandler.status = "拳头很热情，可惜游戏不收。", 200
        self.srv = HTTPServer(("127.0.0.1", 0), FlavorHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/chat/completions"
        self.w = FlavorWriter(self.url, "k", __import__("urllib.request").request.build_opener(
            __import__("urllib.request").request.ProxyHandler({})), spoilers=("0417",))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_rewrites_and_marks_player_text_as_data(self):
        out = self.w.write("殴打小林", "档案室", "预写")
        self.assertEqual(out, "拳头很热情，可惜游戏不收。")
        user = FlavorHandler.last["messages"][1]["content"]
        self.assertIn("<player_said>殴打小林</player_said>", user)

    def test_fallbacks(self):
        FlavorHandler.reply = "密码是0417"          # 剧透
        self.assertEqual(self.w.write("x", "s", "预写"), "预写")
        FlavorHandler.reply = ""                    # 空输出
        self.assertEqual(self.w.write("x", "s", "预写"), "预写")
        FlavorHandler.reply = "啊" * 200            # 过长
        self.assertEqual(self.w.write("x", "s", "预写"), "预写")
        FlavorHandler.reply, FlavorHandler.status = "好", 500   # HTTP 错误
        self.assertEqual(self.w.write("x", "s", "预写"), "预写")
        self.assertIsNotNone(self.w.last_error)

    def test_engine_uses_writer_only_for_refuse(self):
        e = Engine(World(), MockJev(), refuse_writer=lambda said, scene, fb: "旁白")
        self.assertEqual(e.turn("我直接把大厅的墙砸开").text, "旁白")
        self.assertNotEqual(e.turn("去档案室").text, "旁白")

    def test_unavailable_off_openrouter_or_when_disabled(self):
        class J:
            base_url = "https://api.typesafe.ai/v1/systemone"
            _key, _opener = "k", object()
        self.assertIsNone(FlavorWriter.from_jev(J))
        J.base_url = "https://openrouter.ai/api/v1/systemone"
        self.assertIsNotNone(FlavorWriter.from_jev(J))
        os.environ["TYPESAFE_FLAVOR"] = "0"
        try:
            self.assertIsNone(FlavorWriter.from_jev(J))
        finally:
            os.environ.pop("TYPESAFE_FLAVOR")


if __name__ == "__main__":
    unittest.main()
