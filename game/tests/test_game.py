import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from game import verify
from game.engine import Engine, TurnLogger
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
        self.assertEqual(ids, {"go_archive", "examine_shutter", "talk_zhou", "look_around", "check_items"})

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


if __name__ == "__main__":
    unittest.main()
