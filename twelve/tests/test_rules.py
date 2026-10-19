"""规则引擎（trial.py）与判断组装（judge.py）的单元测试。

用一个自己构造的小案子（3 位陪审员、整齐的参数），期望值都能从 trial.py 开头的公式手算：
    quality 2 → qf = 0.5 + 0.125×2 = 0.75，基础分 0；quality 4 → qf = 1.0，基础分 0.1×(4−2) = 0.2。
不访问网络、不读写日志。"""
import copy
import unittest

from twelve.judge import (QUALITY_LEVELS, Judge, Judgment, build_questions, copy_ratio,
                          expand_quotes, parse, state_for)
from twelve.trial import Trial

CONFIG = {
    "time_limit": 100, "win_votes": 2, "lean_min": -2, "lean_max": 2,
    "hit_weight": 1.0, "dislike_weight": 0.5, "base_weight": 0.1,
    "quality_floor": 0.5, "quality_step": 0.125, "quality_center": 2.0,
    "max_delta": 1.5, "focus_limit": 2,
    "repeat_decay": 0.5, "repeat_annoy": 2, "repeat_penalty": 0.1,
    "recite_cut": 0.5, "recite_from": 0.6, "recite_full": 0.8, "recite_base_cut": 0.5,
    "copy_from": 0.5, "copy_full": 0.9,
    "quote_max": 2, "quote_own_min": 6, "quote_own_full": 10,
    "incomplete_cut": 0.5, "incomplete_from": 0.6, "incomplete_full": 0.8,
    "swing_band": 0.5, "manip_threshold": 0.5, "manip_backlash": 0.6,
    "hit_threshold": 0.5, "face_threshold": 0.3, "hint_cost": 10,
}

TEXTS = {
    "manip": "MANIP", "diluted": "DILUTED", "repeated": "REPEATED", "annoyed": "ANNOYED",
    "recited": "RECITED", "cut_off": "CUT_OFF", "cascade": "{name}松口({n})", "silent": "SILENT",
    "hint_concern": "C:{name}/{role}/{concern}", "hint_dislike": "D:{name}/{dislike}",
    "hint_none": "NONE", "win": "WIN", "lose": "LOSE",
}


def juror(jid, start, **extra):
    j = {"id": jid, "name": "名" + jid, "role": "身份" + jid, "start": start,
         "concern": "在意" + jid, "dislike": "雷区" + jid,
         "up": "up" + jid, "down": "down" + jid, "guilty": "g" + jid, "acquit": "a" + jid}
    j.update(extra)
    return j


BASE_CASE = {
    "brief": "案情简介。",
    "dossier": ["案卷一", "案卷二"],
    "dossier_view": [{"text": "简一"}, {"text": "简二"}],
    "arguments": {"x": "论点X", "y": "论点Y"},
    "modes": {"easy": {"label": "精炼", "time_limit": 200, "max_says": 2}},
    "config": CONFIG,
    "texts": TEXTS,
    "jurors": [juror("a", -1.0), juror("b", -0.5), juror("c", 0.5)],
}


def make_case(jurors=None, **cfg):
    case = copy.deepcopy(BASE_CASE)
    if jurors is not None:
        case["jurors"] = copy.deepcopy(jurors)
    case["config"].update(cfg)
    return case


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def J(case, hits=None, dislikes=None, quality=2.0, argument="none", **kw):
    """所有陪审员默认概率 0 的判断。"""
    ids = [j["id"] for j in case["jurors"]]
    hits, dislikes = hits or {}, dislikes or {}
    return Judgment(hits={k: hits.get(k, 0.0) for k in ids},
                    dislikes={k: dislikes.get(k, 0.0) for k in ids},
                    quality=quality, argument=argument, **kw)


class EffectFormulaTests(unittest.TestCase):
    def setUp(self):
        self.case = make_case()
        self.t = Trial(self.case)

    def test_quality_factor_and_base_and_hit_bonus(self):
        # q=4：qf=1.0、基础 0.2；a 戳中 1.0 → 0.2 + 1.0
        e = self.t.effect(J(self.case, hits={"a": 1.0}, quality=4))
        self.assertAlmostEqual(e.deltas["a"], 1.2)
        self.assertAlmostEqual(e.deltas["b"], 0.2)
        self.assertAlmostEqual(e.deltas["c"], 0.2)
        self.assertEqual(e.hit_ids, ["a"])

    def test_low_quality_scales_hit_and_gives_negative_base(self):
        # q=0：qf=0.5、基础 −0.2；a 戳中 0.8 → −0.2 + 0.8×0.5
        e = self.t.effect(J(self.case, hits={"a": 0.8}, quality=0))
        self.assertAlmostEqual(e.deltas["a"], 0.2)
        self.assertAlmostEqual(e.deltas["b"], -0.2)

    def test_quality_center_from_config(self):
        t = Trial(make_case(quality_center=1.0))
        e = t.effect(J(t.case, quality=2))
        self.assertAlmostEqual(e.deltas["a"], 0.1)

    def test_dislike_penalty(self):
        e = self.t.effect(J(self.case, dislikes={"c": 1.0}, quality=4))
        self.assertAlmostEqual(e.deltas["c"], 0.2 - 0.5)
        self.assertAlmostEqual(e.deltas["a"], 0.2)

    def test_hit_below_threshold_still_adds_but_not_in_hit_ids(self):
        e = self.t.effect(J(self.case, hits={"a": 0.4}, quality=2))
        self.assertAlmostEqual(e.deltas["a"], 0.3)
        self.assertEqual(e.hit_ids, [])

    def test_delta_clamped_to_max_delta(self):
        t = Trial(make_case(max_delta=0.5))
        e = t.effect(J(t.case, hits={"a": 1.0}, dislikes={"b": 1.0}, quality=0))
        self.assertAlmostEqual(e.deltas["a"], 0.3)      # −0.2 + 0.5，没到上限
        t2 = Trial(make_case(max_delta=0.25))
        e2 = t2.effect(J(t2.case, hits={"a": 1.0}, dislikes={"b": 1.0}, quality=4))
        self.assertAlmostEqual(e2.deltas["a"], 0.25)    # 1.2 → 0.25
        self.assertAlmostEqual(e2.deltas["b"], -0.25)   # 0.2 − 0.5 = −0.3 → −0.25

    def test_plain_effect_has_no_flags(self):
        e = self.t.effect(J(self.case, hits={"a": 1.0}, quality=2, argument="x"))
        for flag in ("manip", "diluted", "repeated", "annoyed", "recited", "cut_off"):
            self.assertFalse(getattr(e, flag), flag)


class ManipTests(unittest.TestCase):
    def test_manip_above_threshold_backlashes_everyone(self):
        case = make_case()
        t = Trial(case)
        e = t.effect(J(case, hits={"a": 1.0}, quality=4, manip=0.9))
        self.assertTrue(e.manip)
        self.assertEqual(e.deltas, {"a": -0.6, "b": -0.6, "c": -0.6})

    def test_manip_at_threshold_is_not_manip(self):
        case = make_case()
        e = Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, manip=0.5))
        self.assertFalse(e.manip)
        self.assertAlmostEqual(e.deltas["a"], 1.2)


class FocusTests(unittest.TestCase):
    def test_hitting_more_than_focus_limit_dilutes_proportionally(self):
        case = make_case()
        e = Trial(case).effect(J(case, hits={"a": 1.0, "b": 1.0, "c": 1.0}, quality=2))
        self.assertTrue(e.diluted)
        for k in "abc":
            self.assertAlmostEqual(e.deltas[k], 0.75 * 2 / 3)

    def test_hitting_exactly_focus_limit_is_not_diluted(self):
        case = make_case()
        e = Trial(case).effect(J(case, hits={"a": 1.0, "b": 1.0}, quality=2))
        self.assertFalse(e.diluted)
        self.assertAlmostEqual(e.deltas["a"], 0.75)


class RepeatTests(unittest.TestCase):
    def test_repeat_decay_power_of_uses(self):
        case = make_case()
        t = Trial(case)
        t.arg_uses["x"] = 1
        e = t.effect(J(case, hits={"a": 1.0}, quality=2, argument="x"))
        self.assertTrue(e.repeated)
        self.assertFalse(e.annoyed)
        self.assertAlmostEqual(e.deltas["a"], 0.75 * 0.5)

    def test_argument_none_never_decays(self):
        case = make_case()
        t = Trial(case)
        t.arg_uses["none"] = 5
        e = t.effect(J(case, hits={"a": 1.0}, quality=2, argument="none"))
        self.assertFalse(e.repeated)
        self.assertAlmostEqual(e.deltas["a"], 0.75)

    def test_annoyed_from_repeat_annoy_uses(self):
        case = make_case()
        t = Trial(case)
        t.arg_uses["x"] = 2        # 第 3 次讲
        e = t.effect(J(case, hits={"a": 1.0}, quality=4, argument="x"))
        self.assertTrue(e.annoyed)
        self.assertTrue(e.repeated)
        # 基础 0.2 → min(0.2, 0) − 0.1 = −0.1；戳中 1.0×1.0×0.5² = 0.25
        self.assertAlmostEqual(e.deltas["b"], -0.1)
        self.assertAlmostEqual(e.deltas["a"], 0.15)

    def test_annoyed_keeps_negative_base(self):
        case = make_case()
        t = Trial(case)
        t.arg_uses["x"] = 2
        e = t.effect(J(case, quality=0, argument="x"))
        self.assertAlmostEqual(e.deltas["b"], -0.3)


class ReciteTests(unittest.TestCase):
    def eff(self, quality=4, **kw):
        case = make_case()
        return Trial(case).effect(J(case, hits={"a": 1.0}, quality=quality, **kw))

    def test_recite_below_from_not_penalised(self):
        e = self.eff(recite=0.5)
        self.assertAlmostEqual(e.deltas["a"], 1.2)
        self.assertFalse(e.recited)

    def test_recite_halfway_ramp(self):
        # rr = (0.7−0.6)/0.2 = 0.5；戳中 ×(1−0.5×0.5)=0.75；基础 0.2×(1−0.5×0.5)=0.15
        e = self.eff(recite=0.7)
        self.assertAlmostEqual(e.deltas["a"], 0.15 + 0.75)
        self.assertAlmostEqual(e.deltas["b"], 0.15)
        self.assertTrue(e.recited)

    def test_recite_full_penalty(self):
        e = self.eff(recite=0.9)
        self.assertAlmostEqual(e.deltas["a"], 0.1 + 0.5)
        self.assertAlmostEqual(e.deltas["b"], 0.1)

    def test_recited_flag_threshold(self):
        self.assertFalse(self.eff(recite=0.65).recited)   # rr = 0.25
        self.assertTrue(self.eff(recite=0.67).recited)    # rr = 0.35

    def test_copied_ramp(self):
        # copied 0.7 → (0.7−0.5)/0.4 = 0.5，和 recite 0.7 一样
        e = self.eff(copied=0.7)
        self.assertAlmostEqual(e.deltas["a"], 0.9)
        self.assertTrue(e.recited)
        self.assertAlmostEqual(self.eff(copied=0.5).deltas["a"], 1.2)

    def test_recite_and_copied_take_max(self):
        e1 = self.eff(recite=0.9, copied=0.7)
        e2 = self.eff(recite=0.7, copied=0.9)
        self.assertAlmostEqual(e1.deltas["a"], 0.6)
        self.assertAlmostEqual(e2.deltas["a"], 0.6)
        e3 = self.eff(recite=0.7, copied=0.7)      # 取大不相加：仍是 0.5
        self.assertAlmostEqual(e3.deltas["a"], 0.9)

    def test_recite_does_not_shrink_negative_base(self):
        e = self.eff(quality=0, recite=0.9)
        self.assertAlmostEqual(e.deltas["b"], -0.2)
        # 戳中：1.0×0.5(qf)×0.5(念卷) = 0.25
        self.assertAlmostEqual(e.deltas["a"], -0.2 + 0.25)


class QuoteTests(unittest.TestCase):
    def eff(self, **kw):
        case = make_case()
        return Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, **kw))

    def test_enough_own_chars_no_penalty(self):
        self.assertAlmostEqual(self.eff(quoted=1, own_chars=10).deltas["a"], 1.2)

    def test_own_chars_halfway_counts_as_half_recite(self):
        # own = (8−6)/4 = 0.5 → rr = 0.5
        e = self.eff(quoted=1, own_chars=8)
        self.assertAlmostEqual(e.deltas["a"], 0.9)
        self.assertTrue(e.recited)

    def test_too_few_own_chars_full_recite(self):
        e = self.eff(quoted=2, own_chars=6)
        self.assertAlmostEqual(e.deltas["a"], 0.6)
        self.assertAlmostEqual(e.deltas["b"], 0.1)

    def test_no_quote_ignores_own_chars(self):
        self.assertAlmostEqual(self.eff(quoted=0, own_chars=0).deltas["a"], 1.2)


class IncompleteTests(unittest.TestCase):
    def test_spoken_incomplete_cuts_hit_and_zeroes_base(self):
        case = make_case()
        # cut = (0.75−0.6)/0.2 = 0.75；戳中 ×(1−0.5×0.75)=0.625；基础 min(0.2,0)=0
        e = Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, incomplete=0.75))
        self.assertAlmostEqual(e.deltas["a"], 0.625)
        self.assertAlmostEqual(e.deltas["b"], 0.0)
        self.assertTrue(e.cut_off)

    def test_small_incomplete_ramp_not_cut_off_flag(self):
        case = make_case()
        # cut = 0.25：戳中 ×0.875，基础照样归零，但不算 cut_off
        e = Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, incomplete=0.65))
        self.assertAlmostEqual(e.deltas["a"], 0.875)
        self.assertAlmostEqual(e.deltas["b"], 0.0)
        self.assertFalse(e.cut_off)

    def test_below_from_ignored(self):
        case = make_case()
        e = Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, incomplete=0.6))
        self.assertAlmostEqual(e.deltas["a"], 1.2)

    def test_preview_ignores_incomplete(self):
        case = make_case()
        e = Trial(case).effect(J(case, hits={"a": 1.0}, quality=4, incomplete=0.95), spoken=False)
        self.assertAlmostEqual(e.deltas["a"], 1.2)
        self.assertFalse(e.cut_off)

    def test_cut_off_speech_not_counted_as_argument_use(self):
        case = make_case()
        t = Trial(case)
        sp = t.speak("话没说完，", J(case, hits={"a": 1.0}, quality=4, argument="x", incomplete=0.95))
        self.assertEqual(t.arg_uses, {})
        self.assertEqual(t.touched, set())
        self.assertIn("CUT_OFF", sp.notes)
        self.assertNotIn("SILENT", sp.notes)


class FacesPreviewTests(unittest.TestCase):
    def test_faces_three_levels_strict_threshold(self):
        t = Trial(make_case())
        self.assertEqual(t.faces({"a": 0.31, "b": 0.3, "c": -0.3, "d": -0.31, "e": 0.0}),
                         {"a": 1, "b": 0, "c": 0, "d": -1, "e": 0})

    def test_preview_returns_faces(self):
        case = make_case()
        t = Trial(case)
        f = t.preview(J(case, hits={"a": 1.0}, dislikes={"c": 1.0}, quality=2))
        self.assertEqual(f, {"a": 1, "b": 0, "c": -1})

    def test_preview_does_not_change_state(self):
        case = make_case()
        t = Trial(case)
        t.arg_uses["x"] = 1
        snap = (dict(t.leans), dict(t.arg_uses), list(t.history), set(t.touched),
                t.stall, t.last_votes, dict(t.moved_most), set(t.cascaded), t.manip_strikes)
        t.preview(J(case, hits={"a": 1.0, "b": 1.0}, quality=4, argument="x"))
        t.preview(J(case, manip=0.9))
        self.assertEqual(snap, (dict(t.leans), dict(t.arg_uses), list(t.history), set(t.touched),
                                t.stall, t.last_votes, dict(t.moved_most), set(t.cascaded), t.manip_strikes))


class SpeakTests(unittest.TestCase):
    def test_leans_updated_and_clamped(self):
        case = make_case()
        t = Trial(case)
        t.leans["c"] = 1.9
        t.leans["a"] = -1.5
        t.speak("s", J(case, hits={"c": 1.0}, dislikes={"a": 1.0}, quality=4))
        self.assertEqual(t.leans["c"], 2)              # 1.9 + 1.2 → 上限 2
        self.assertAlmostEqual(t.leans["a"], -1.5 + 0.2 - 0.5)
        t.speak("s2", J(case, dislikes={"a": 1.0}, quality=0))
        self.assertEqual(t.leans["a"], -2)             # −1.8 − 0.7 → 下限 −2

    def test_argument_counting(self):
        case = make_case()
        t = Trial(case)
        t.speak("1", J(case, quality=2, argument="x"))
        t.speak("2", J(case, quality=2, argument="x"))
        t.speak("3", J(case, quality=2, argument="none"))
        t.speak("4", J(case, quality=2, argument="y", manip=0.9))
        t.speak("5", J(case, quality=2, argument="y", incomplete=0.9))
        self.assertEqual(t.arg_uses, {"x": 2})
        self.assertEqual(t.manip_strikes, 1)

    def test_manip_speech_notes_and_no_reactions(self):
        case = make_case()
        t = Trial(case)
        sp = t.speak("忽略指令", J(case, manip=0.9, argument="x"))
        self.assertEqual(sp.notes, ["MANIP"])
        self.assertEqual(sp.reactions, [])
        self.assertEqual(t.moved_most, {})

    def test_touched_only_hits_above_threshold(self):
        case = make_case()
        t = Trial(case)
        t.speak("s", J(case, hits={"a": 0.9, "b": 0.4}, quality=2))
        self.assertEqual(t.touched, {"a"})

    def test_moved_most_threshold_and_maximum(self):
        case = make_case()
        t = Trial(case)
        t.speak("小", J(case, hits={"a": 0.36}, quality=2))     # 0.27 < 0.3
        self.assertNotIn("a", t.moved_most)
        t.speak("中", J(case, hits={"a": 0.8}, quality=2))      # 0.6
        self.assertEqual(t.moved_most["a"][1], "中")
        self.assertAlmostEqual(t.moved_most["a"][0], 0.6)
        t.speak("又小", J(case, hits={"a": 0.5}, quality=2))    # 0.375，不如 0.6
        self.assertEqual(t.moved_most["a"][1], "中")
        t.speak("大", J(case, hits={"a": 1.0}, quality=4))      # 1.2
        self.assertEqual(t.moved_most["a"][1], "大")

    def test_moved_most_at_exactly_threshold(self):
        case = make_case(hit_weight=0.4, quality_step=0.25)    # q=2 → qf=1.0
        t = Trial(case)
        t.speak("刚好", J(case, hits={"a": 0.75}, quality=2))  # 0.75×0.4×1.0 = 0.3
        self.assertEqual(t.moved_most["a"][1], "刚好")

    def test_reaction_thresholds(self):
        case = make_case()
        t = Trial(case)
        sp = t.speak("s", J(case, hits={"a": 1.0, "c": 0.6}, dislikes={"b": 1.0}, quality=2))
        # a 0.75 ↑，b −0.5 ↓（刚好），c 0.45 没反应
        self.assertEqual(sp.reactions, [{"juror": "a", "dir": 1, "text": "upa"},
                                        {"juror": "b", "dir": -1, "text": "downb"}])
        self.assertNotIn("SILENT", sp.notes)

    def test_silent_note_when_nobody_reacts(self):
        case = make_case()
        sp = Trial(case).speak("s", J(case, quality=2))
        self.assertEqual(sp.reactions, [])
        self.assertIn("SILENT", sp.notes)

    def test_stall_counts_and_resets(self):
        case = make_case()
        t = Trial(case)
        self.assertEqual(t.votes(), 1)
        t.speak("1", J(case, quality=2))
        self.assertEqual(t.stall, 1)
        t.speak("2", J(case, quality=2))
        self.assertEqual(t.stall, 2)
        sp = t.speak("3", J(case, hits={"b": 1.0}, quality=2))   # b −0.5 + 0.75 → 0.25
        self.assertEqual(sp.votes, 2)
        self.assertEqual(t.stall, 0)
        t.speak("4", J(case, dislikes={"c": 1.0}, quality=2))   # 票数掉了也算卡住
        self.assertEqual(t.stall, 1)

    def test_history_records(self):
        case = make_case()
        t = Trial(case)
        t.speak("话", J(case, hits={"a": 1.0}, quality=3, argument="x", recite=0.12345))
        h = t.history[0]
        self.assertEqual(h["text"], "话")
        self.assertEqual(h["argument"], "x")
        self.assertEqual(h["quality"], 3)
        self.assertEqual(h["recite"], 0.123)
        self.assertEqual(h["votes"], 1)


CASCADE_JURORS = [juror("a", -0.5, influence=0.5), juror("b", -1.0), juror("c", -0.2),
                  juror("d", 0.5), juror("e", -0.03)]


class CascadeTests(unittest.TestCase):
    def test_hardliner_flip_pushes_others_up_to_minus_005(self):
        case = make_case(CASCADE_JURORS)
        t = Trial(case)
        sp = t.speak("s", J(case, hits={"a": 1.0}, quality=2))   # a −0.5 + 0.75 → 0.25
        self.assertAlmostEqual(t.leans["a"], 0.25)
        self.assertAlmostEqual(t.leans["b"], -0.5)       # −1 + 0.5
        self.assertAlmostEqual(t.leans["c"], -0.05)      # −0.2 + 0.5 → 封顶 −0.05，不翻
        self.assertEqual(t.leans["d"], 0.5)              # 已经无罪，不动
        self.assertEqual(t.leans["e"], -0.03)            # 已经高于 −0.05，不动
        self.assertAlmostEqual(sp.deltas["b"], 0.5)
        self.assertAlmostEqual(sp.deltas["c"], 0.15)
        self.assertEqual(sp.cascade, [{"juror": "a", "moved": ["b", "c"]}])
        self.assertEqual(sp.votes, 2)
        self.assertIn("名a松口(2)", sp.notes)
        self.assertEqual(t.cascaded, {"a"})

    def test_cascade_only_once_per_juror(self):
        case = make_case(CASCADE_JURORS)
        t = Trial(case)
        t.speak("s", J(case, hits={"a": 1.0}, quality=2))
        t.leans["a"] = -0.5
        b = t.leans["b"]
        sp = t.speak("s2", J(case, hits={"a": 1.0}, quality=2))
        self.assertEqual(sp.cascade, [])
        self.assertEqual(t.leans["b"], b)

    def test_no_cascade_if_already_acquitting(self):
        jurors = [juror("a", 0.1, influence=0.5), juror("b", -1.0)]
        case = make_case(jurors)
        t = Trial(case)
        sp = t.speak("s", J(case, hits={"a": 1.0}, quality=2))
        self.assertEqual(sp.cascade, [])
        self.assertEqual(t.leans["b"], -1.0)

    def test_no_cascade_without_influence(self):
        jurors = [juror("a", -0.5), juror("b", -1.0)]
        case = make_case(jurors)
        t = Trial(case)
        t.speak("s", J(case, hits={"a": 1.0}, quality=2))
        self.assertEqual(t.leans["b"], -1.0)

    def test_two_hardliners_same_sentence_take_max(self):
        jurors = [juror("a", -0.5, influence=0.5), juror("f", -0.5, influence=0.3), juror("b", -1.5)]
        case = make_case(jurors)
        t = Trial(case)
        sp = t.speak("s", J(case, hits={"a": 1.0, "f": 1.0}, quality=2))
        self.assertAlmostEqual(t.leans["b"], -1.0)      # +0.5，不是 +0.8
        self.assertEqual(t.cascaded, {"a", "f"})
        self.assertEqual([c["juror"] for c in sp.cascade], ["a", "f"])

    def test_cascade_flip_does_not_chain(self):
        # g 是硬骨头但只靠连带（被推到 −0.05）不会翻，也就不会触发自己的连带
        jurors = [juror("a", -0.5, influence=0.5), juror("g", -0.3, influence=1.0), juror("b", -1.9)]
        case = make_case(jurors)
        t = Trial(case)
        t.speak("s", J(case, hits={"a": 1.0}, quality=2))
        self.assertAlmostEqual(t.leans["g"], -0.05)
        self.assertAlmostEqual(t.leans["b"], -1.4)
        self.assertEqual(t.cascaded, {"a"})


class EventTests(unittest.TestCase):
    EV = {"id": "e1", "dossier": ["案卷三"], "dossier_view": [{"text": "简三"}],
          "arguments": {"z": "论点Z"},
          "jurors": [{"id": "a", "concern": "新在意", "dislike": "新雷区"}],
          "leans": {"a": 0.5, "b": -5}, "leans_all": 0.25, "time": 20}

    def test_event_updates_data(self):
        case = make_case()
        t = Trial(case)
        t.apply_event(copy.deepcopy(self.EV))
        self.assertEqual(case["dossier"], ["案卷一", "案卷二", "案卷三"])
        self.assertEqual([v["text"] for v in case["dossier_view"]], ["简一", "简二", "简三"])
        self.assertEqual(case["arguments"]["z"], "论点Z")
        self.assertEqual(case["jurors"][0]["concern"], "新在意")
        self.assertEqual(case["jurors"][0]["dislike"], "新雷区")
        self.assertEqual(case["jurors"][0]["id"], "a")
        self.assertAlmostEqual(t.leans["a"], -1.0 + 0.5 + 0.25)
        self.assertEqual(t.leans["b"], -2)                # 截断在 lean_min
        self.assertAlmostEqual(t.leans["c"], 0.75)
        self.assertEqual(t.events, ["e1"])

    def test_event_without_dossier_view_in_case(self):
        case = make_case()
        del case["dossier_view"]
        Trial(case).apply_event({"id": "e", "dossier": ["案卷三"]})
        self.assertNotIn("dossier_view", case)
        self.assertEqual(len(case["dossier"]), 3)

    def test_event_time_extends_clock(self):
        clock = Clock()
        case = make_case()
        t = Trial(case, clock=clock)
        t.begin()
        clock.t += 30
        t.apply_event({"id": "e", "time": 20})
        self.assertAlmostEqual(t.time_left(), 100 - 30 + 20)

    def test_event_vote_change_is_not_stall(self):
        case = make_case()
        t = Trial(case)
        t.apply_event({"id": "e", "leans": {"c": -1.0}})   # c 0.5 → −0.5，票数 1 → 0
        self.assertEqual(t.last_votes, 0)
        t.speak("s", J(case, hits={"b": 1.0}, quality=2))  # b 翻，票数 0 → 1：涨了
        self.assertEqual(t.stall, 0)

    def test_event_does_not_touch_other_trials(self):
        base = make_case()
        c1, c2 = copy.deepcopy(base), copy.deepcopy(base)
        Trial(c1).apply_event({"id": "e", "dossier": ["新"]})
        self.assertEqual(len(c2["dossier"]), 2)


class HintTests(unittest.TestCase):
    def test_hint_costs_time_unless_free(self):
        clock = Clock()
        case = make_case()
        t = Trial(case, clock=clock)
        t.begin()
        t.hint()
        self.assertEqual(t.time_left(), 90)
        t.hint(free=True)
        self.assertEqual(t.time_left(), 90)

    def test_hint_picks_closest_guilty_concern_then_dislike(self):
        case = make_case()
        t = Trial(case)
        h1 = t.hint()
        self.assertEqual(h1, {"juror": "b", "text": "C:名b/身份b/在意b"})
        h2 = t.hint()
        self.assertEqual(h2, {"juror": "b", "text": "D:名b/雷区b"})
        h3 = t.hint()
        self.assertEqual(h3, {"juror": "a", "text": "C:名a/身份a/在意a"})
        t.hint()
        h5 = t.hint()            # 都提示完了：回到离 0 最近的人的在意点
        self.assertEqual(h5, {"juror": "b", "text": "C:名b/身份b/在意b"})

    def test_hint_skips_concern_for_touched_juror(self):
        case = make_case()
        t = Trial(case)
        t.touched.add("b")
        self.assertEqual(t.hint(), {"juror": "b", "text": "D:名b/雷区b"})

    def test_hint_zero_lean_counts_as_guilty(self):
        case = make_case()
        t = Trial(case)
        t.leans["c"] = 0.0
        self.assertEqual(t.hint()["juror"], "c")

    def test_hint_when_everyone_acquits(self):
        case = make_case()
        t = Trial(case)
        for k in t.leans:
            t.leans[k] = 1.0
        self.assertEqual(t.hint(), {"juror": None, "text": "NONE"})


class VerdictTests(unittest.TestCase):
    def test_votes_strictly_positive(self):
        t = Trial(make_case())
        t.leans.update({"a": 0.0, "b": 0.01, "c": -0.01})
        self.assertEqual(t.votes(), 1)

    def test_win_votes_boundary(self):
        t = Trial(make_case())
        t.leans.update({"a": -1, "b": 0.1, "c": 0.5})
        v = t.verdict()
        self.assertTrue(v["acquit"])
        self.assertEqual((v["votes"], v["need"], v["text"]), (2, 2, "WIN"))
        t.leans["b"] = 0.0
        v = t.verdict()
        self.assertFalse(v["acquit"])
        self.assertEqual(v["text"], "LOSE")

    def test_swing_band(self):
        t = Trial(make_case())
        t.leans.update({"a": -0.5, "b": -0.49, "c": 0.0})
        js = {j["id"]: j for j in t.verdict()["jurors"]}
        self.assertFalse(js["a"]["swing"])
        self.assertTrue(js["b"]["swing"])
        self.assertTrue(js["c"]["swing"])
        self.assertFalse(js["c"]["acquit"])
        self.assertEqual(js["c"]["line"], "gc")
        t.leans["c"] = 0.1
        js = {j["id"]: j for j in t.verdict()["jurors"]}
        self.assertFalse(js["c"]["swing"])
        self.assertEqual(js["c"]["line"], "ac")

    def test_best_line_largest_positive_sum(self):
        case = make_case()
        t = Trial(case)
        t.speak("一个", J(case, hits={"a": 1.0}, quality=2))              # 正向和 0.75
        t.speak("两个", J(case, hits={"a": 1.0, "b": 0.6}, quality=2))    # 0.75 + 0.45 = 1.2
        t.speak("差的", J(case, dislikes={"a": 1.0, "b": 1.0}, quality=0))
        best = t.verdict()["best"]
        self.assertEqual(best, {"text": "两个", "moved": ["名a"]})

    def test_best_ignores_manip_and_small_gain(self):
        t = Trial(make_case())
        t.history = [
            {"text": "操纵", "manip": 0.9, "deltas": {"a": 1.5, "b": 1.5, "c": 1.5}},
            {"text": "刚好", "manip": 0.0, "deltas": {"a": 0.3, "b": -1.0, "c": 0.0}},
        ]
        self.assertIsNone(t.verdict()["best"])
        t.history.append({"text": "过线", "manip": 0.0, "deltas": {"a": 0.31, "b": 0.0, "c": 0.0}})
        self.assertEqual(t.verdict()["best"], {"text": "过线", "moved": []})

    def test_by_only_for_originally_guilty_now_acquitting(self):
        case = make_case()
        t = Trial(case)
        t.speak("翻b", J(case, hits={"b": 1.0, "c": 1.0}, quality=2))   # b → 0.25；c 原本就无罪
        t.speak("动a", J(case, hits={"a": 1.0}, quality=2))             # a → −0.25，仍有罪
        js = {j["id"]: j for j in t.verdict()["jurors"]}
        self.assertEqual(js["b"]["by"], "翻b")
        self.assertIsNone(js["c"]["by"])
        self.assertIsNone(js["a"]["by"])
        self.assertEqual(t.verdict()["says"], 2)


class ClockModeTests(unittest.TestCase):
    def test_time_left_before_and_after_begin(self):
        clock = Clock()
        t = Trial(make_case(), clock=clock)
        t.penalty = 10
        self.assertEqual(t.time_left(), 100.0)     # 开庭前满格，不扣罚时
        self.assertFalse(t.over)
        t.begin()
        clock.t += 30
        self.assertEqual(t.time_left(), 60)
        t.begin()                                   # 再 begin 不重置
        self.assertEqual(t.time_left(), 60)
        clock.t += 500
        self.assertEqual(t.time_left(), 0.0)
        self.assertTrue(t.over)

    def test_over_when_rested(self):
        t = Trial(make_case(), clock=Clock())
        t.begin()
        self.assertFalse(t.over)
        t.rested = True
        self.assertTrue(t.over)

    def test_out_of_says(self):
        case = make_case(max_says=2)
        t = Trial(case, clock=Clock())
        t.begin()
        t.speak("1", J(case))
        self.assertFalse(t.out_of_says)
        t.speak("2", J(case))
        self.assertTrue(t.out_of_says)
        self.assertTrue(t.over)
        self.assertFalse(Trial(make_case(max_says=0)).out_of_says)

    def test_set_mode(self):
        case = make_case()
        clock = Clock()
        t = Trial(case, clock=clock)
        self.assertFalse(t.set_mode("hard"))
        self.assertEqual(t.mode, "normal")
        self.assertTrue(t.set_mode("easy"))
        self.assertEqual(t.mode, "easy")
        self.assertEqual(t.cfg["time_limit"], 200)
        self.assertEqual(t.cfg["max_says"], 2)
        self.assertNotIn("label", t.cfg)
        self.assertEqual(t.time_left(), 200.0)

    def test_set_mode_refused_after_begin(self):
        case = make_case()
        t = Trial(case, clock=Clock())
        t.begin()
        self.assertFalse(t.set_mode("easy"))
        self.assertEqual(t.cfg["time_limit"], 100)
        self.assertEqual(t.mode, "normal")


# ---------------- judge.py ----------------

D16 = "一二三四五六七八九十甲乙丙丁戊己"


class CopyRatioTests(unittest.TestCase):
    def test_copy_ratio_counts_long_runs(self):
        # 照搬前 14 字 + 自己 3 字
        self.assertAlmostEqual(copy_ratio("一二三四五六七八九十甲乙丙丁，我认为", [D16]), 14 / 17)

    def test_punctuation_ignored(self):
        self.assertEqual(copy_ratio("一二三，四五六。七八九 十甲乙丙丁", ["一二三四五六七八九十甲乙丙丁"]), 1.0)
        self.assertEqual(copy_ratio("一二三四五六七八九十甲乙丙丁", ["一二三四五六，七八九十甲乙丙丁！"]), 1.0)

    def test_short_runs_not_counted(self):
        # 每段只连续 5 字与案卷一致
        text = "一二三四五我六七八九十你甲乙丙丁戊他"
        self.assertEqual(copy_ratio(text, [D16]), 0.0)
        self.assertGreater(copy_ratio(text, [D16], min_run=5, min_chars=10), 0.0)
        self.assertAlmostEqual(copy_ratio(text, [D16], min_run=5, min_chars=10), 15 / 18)

    def test_below_min_chars_is_zero(self):
        self.assertEqual(copy_ratio("一二三四五六七八九十甲乙丙我说", [D16]), 0.0)   # 照搬 13 字
        self.assertAlmostEqual(copy_ratio("一二三四五六七八九十甲乙丙我说", [D16], min_chars=13), 13 / 15)

    def test_empty_text(self):
        self.assertEqual(copy_ratio("", [D16]), 0.0)
        self.assertEqual(copy_ratio("，。！", [D16]), 0.0)

    def test_runs_from_multiple_entries(self):
        d = ["一二三四五六七", "甲乙丙丁戊己庚"]
        self.assertEqual(copy_ratio("一二三四五六七甲乙丙丁戊己庚", d), 1.0)


class ExpandQuotesTests(unittest.TestCase):
    DOSSIER = ["张伟两点在A区刷卡，B区要走八分钟", "原文二"]
    VIEW = [{"text": "张伟两点在A区刷卡"}, {"text": "简二"}]

    def test_view_text_with_brackets_replaced_by_original(self):
        full, own, n = expand_quotes("「简二」所以他无罪", self.DOSSIER, 2, self.VIEW)
        self.assertEqual(full, "「原文二」所以他无罪")
        self.assertEqual(own, "所以他无罪")
        self.assertEqual(n, 1)

    def test_original_text_without_brackets(self):
        full, own, n = expand_quotes("原文二说明问题", self.DOSSIER, 2, self.VIEW)
        self.assertEqual(full, "「原文二」说明问题")
        self.assertEqual(own, "说明问题")
        self.assertEqual(n, 1)

    def test_longer_candidate_first(self):
        text = "「张伟两点在A区刷卡，B区要走八分钟」来不及"
        full, own, n = expand_quotes(text, self.DOSSIER, 2, self.VIEW)
        self.assertEqual(full, "「张伟两点在A区刷卡，B区要走八分钟」来不及")
        self.assertEqual(own, "来不及")
        self.assertEqual(n, 1)

    def test_short_view_expands_to_original(self):
        full, own, n = expand_quotes("「张伟两点在A区刷卡」来不及", self.DOSSIER, 2, self.VIEW)
        self.assertEqual(full, "「张伟两点在A区刷卡，B区要走八分钟」来不及")
        self.assertEqual(own, "来不及")

    def test_over_max_quotes_left_as_own_text(self):
        d = ["甲甲甲甲", "乙乙乙乙乙", "丙丙丙丙丙丙"]
        view = [{"text": "小甲"}, {"text": "小乙"}, {"text": "小丙"}]
        text = "「小甲」「乙乙乙乙乙」「丙丙丙丙丙丙」好"
        full, own, n = expand_quotes(text, d, 2, view)
        self.assertEqual(n, 2)
        # 先找长的：丙、乙被展开；「小甲」超出条数，不展开也不删
        self.assertEqual(own, "「小甲」好")
        self.assertEqual(full, "「小甲」「乙乙乙乙乙」「丙丙丙丙丙丙」好")

    def test_same_quote_twice_counts_twice(self):
        full, own, n = expand_quotes("简二和简二", self.DOSSIER, 2, self.VIEW)
        self.assertEqual((full, own, n), ("「原文二」和「原文二」", "和", 2))

    def test_legacy_marker(self):
        full, own, n = expand_quotes("〔案卷2〕说明", self.DOSSIER, 2)
        self.assertEqual((full, own, n), ("「原文二」说明", "说明", 1))

    def test_legacy_marker_bad_number_removed(self):
        full, own, n = expand_quotes("〔案卷9〕说明", self.DOSSIER, 2)
        self.assertEqual((full, own, n), ("说明", "说明", 0))

    def test_legacy_marker_over_max_removed(self):
        full, own, n = expand_quotes("〔案卷1〕〔案卷2〕好", self.DOSSIER, 1)
        self.assertEqual(n, 1)
        self.assertEqual(own, "好")
        self.assertEqual(full, "「张伟两点在A区刷卡，B区要走八分钟」好")

    def test_no_quote(self):
        self.assertEqual(expand_quotes("我自己的话", self.DOSSIER, 2, self.VIEW),
                         ("我自己的话", "我自己的话", 0))


class QuestionsTests(unittest.TestCase):
    def test_build_questions_keys_and_types(self):
        case = make_case([juror("a", 0), juror("b", 0)])
        q = build_questions(case)
        self.assertEqual(set(q), {"hit_a", "dis_a", "hit_b", "dis_b", "quality", "argument",
                                  "manip", "recite", "incomplete"})
        for k in ("hit_a", "dis_a", "hit_b", "dis_b", "manip", "recite", "incomplete"):
            self.assertEqual(q[k]["type"], "noul", k)
        self.assertEqual(q["quality"]["type"], "score")
        self.assertEqual(q["quality"]["criteria"], QUALITY_LEVELS)
        self.assertEqual(q["argument"]["type"], "choice")
        self.assertEqual(q["argument"]["criteria"], {"x": "论点X", "y": "论点Y"})
        self.assertEqual(q["hit_a"]["instructions"]["concern"], "在意a")
        self.assertEqual(q["dis_b"]["instructions"]["trait"], "雷区b")

    def test_state_for_has_brief_and_all_dossier(self):
        case = make_case()
        s = state_for(case, "我的话")
        self.assertEqual(s["player_said"], "我的话")
        for part in ["案情简介。", "案卷一", "案卷二"]:
            self.assertIn(part, s["case"])


class ParseTests(unittest.TestCase):
    def test_defaults(self):
        case = make_case()
        jd = parse(case, {})
        self.assertEqual(jd.quality, 2.0)
        self.assertEqual(jd.argument, "none")
        self.assertEqual(jd.argument_p, 0.0)
        self.assertEqual(jd.hits, {"a": 0.0, "b": 0.0, "c": 0.0})
        self.assertEqual(jd.dislikes, {"a": 0.0, "b": 0.0, "c": 0.0})
        self.assertEqual((jd.manip, jd.recite, jd.incomplete), (0.0, 0.0, 0.0))

    def test_values(self):
        case = make_case()
        ans = {"hit_a": {"noul": 0.9}, "dis_b": {"noul": 0.3}, "quality": {"score": 3.5},
               "argument": {"choice": "y", "probabilities": {"y": 0.8, "x": 0.2}},
               "manip": {"noul": 0.1}, "recite": {"noul": 0.7}, "incomplete": {"noul": 0.2}}
        jd = parse(case, ans, ms=12.0)
        self.assertEqual(jd.hits["a"], 0.9)
        self.assertEqual(jd.dislikes["b"], 0.3)
        self.assertEqual(jd.quality, 3.5)
        self.assertEqual((jd.argument, jd.argument_p), ("y", 0.8))
        self.assertEqual((jd.manip, jd.recite, jd.incomplete, jd.ms), (0.1, 0.7, 0.2, 12.0))

    def test_unknown_argument_becomes_none(self):
        case = make_case()
        jd = parse(case, {"argument": {"choice": "zzz", "probabilities": {"zzz": 0.9}}})
        self.assertEqual(jd.argument, "none")
        self.assertEqual(jd.argument_p, 0.0)


class FakeClient:
    def __init__(self, answers=None, usage=None, is_local=False, last_ms=33.0):
        self.answers = answers or {}
        self.usage = usage
        self.is_local = is_local
        self.last_ms = last_ms
        self.states = []

    def ask(self, state, questions):
        self.states.append(state)
        resp = {"answers": self.answers}
        if self.usage is not None:
            resp["usage"] = self.usage
        return resp


class JudgeTests(unittest.TestCase):
    def case(self, quote_max):
        case = make_case(quote_max=quote_max)
        case["dossier"] = [D16, "原文二"]
        case["dossier_view"] = [{"text": "简一"}, {"text": "简二"}]
        return case

    def test_quote_max_zero_passes_text_through(self):
        case = self.case(0)
        client = FakeClient(answers={"quality": {"score": 3.0}, "hit_a": {"noul": 0.8}})
        text = "「" + D16 + "」我说"
        jd = Judge(case, client).judge(text)
        self.assertEqual(client.states[0]["player_said"], text)
        self.assertEqual(jd.quoted, 0)
        # 注意：_PUNCT 不含「」，不开引用时玩家打的「」也算字：16 + 2 + 2
        self.assertEqual(jd.own_chars, 20)
        self.assertAlmostEqual(jd.copied, 16 / 20)
        self.assertEqual(jd.quality, 3.0)
        self.assertEqual(jd.hits["a"], 0.8)
        self.assertEqual(jd.ms, 33.0)

    def test_quotes_expanded_and_copied_only_on_own_text(self):
        case = self.case(2)
        client = FakeClient()
        jd = Judge(case, client).judge("「简一」说明，他根本不在场！")
        self.assertEqual(client.states[0]["player_said"], "「" + D16 + "」说明，他根本不在场！")
        self.assertEqual(jd.quoted, 1)
        self.assertEqual(jd.own_chars, 8)               # 说明他根本不在场 去标点
        self.assertEqual(jd.copied, 0.0)

    def test_quoting_full_original_not_counted_as_copied(self):
        case = self.case(2)
        jd = Judge(case, FakeClient()).judge(D16 + "所以他无罪")
        self.assertEqual(jd.quoted, 1)
        self.assertEqual(jd.own_chars, 5)
        self.assertEqual(jd.copied, 0.0)

    def test_two_quotes_counted(self):
        case = self.case(2)
        jd = Judge(case, FakeClient()).judge("「简一」「简二」矛盾")
        self.assertEqual((jd.quoted, jd.own_chars), (2, 2))

    def test_cost_from_usage(self):
        case = self.case(0)
        self.assertEqual(Judge(case, FakeClient(usage={"cost": 0.5, "input_tokens": 1000})).judge("话").cost, 0.5)

    def test_cost_estimated_from_input_tokens(self):
        case = self.case(0)
        jd = Judge(case, FakeClient(usage={"input_tokens": 1000})).judge("话")
        self.assertAlmostEqual(jd.cost, 1000 * 0.042e-6)

    def test_cost_zero_without_usage(self):
        self.assertEqual(Judge(self.case(0), FakeClient()).judge("话").cost, 0.0)

    def test_cost_zero_for_local_model(self):
        jd = Judge(self.case(0), FakeClient(usage={"cost": 0.5}, is_local=True)).judge("话")
        self.assertEqual(jd.cost, 0.0)


if __name__ == "__main__":
    unittest.main()
