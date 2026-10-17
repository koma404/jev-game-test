"""第 2 关《黄衣服》开场的 5 个镜头。python -m twelve.art.case2 生成 twelve/art/case2_1..5.json

不剧透庭审中途的反转（提前点送达、车牌截图）：开场只交代撞人、目击证人、满街的黄衣服。"""
from __future__ import annotations

import random
from pathlib import Path

from .case1 import courtroom
from .pix import Pix

OUT = Path(__file__).parent

YEL, YEL_D, YEL_L = "#e0a81e", "#9a6e10", "#ffd860"        # 骑手服
ROAD, ROAD_L, WALK = "#15171f", "#1c1f29", "#e6e6dc"


def road_x(y, horizon=44, left=66, right=94, spread=1.5):
    """透视的马路：第 y 行左右边缘的 x。"""
    k = (y - horizon) * spread
    return left - k, right + k


def rider(p: Pix, x, y, scale=1, face="#2a1c14", lit=True):
    """骑电动车的外卖骑手，朝右：黄头盔、黄衣服、背后黄色外卖箱。"""
    rows = ["..........hhhh.....",
            ".........hhhhhh....",
            ".........hhhffff...",
            "..........jffff....",
            "..bbbbb..jjjj......",
            "..bbbbb.jjjjjj.....",
            "..bbbbb.jjjjjjaa...",
            "..bbbbb.jjjjjj.aa..",
            "..bbbbb.jjjjjj..aam",
            "..rrrrrrkkkkkkk..m.",
            ".......kkkkkkkkk.m.",
            "..ww...kk.....kkmw.",
            ".w..w..eeeeeeeeew.w",
            ".w..w...........w.w",
            "..ww.............w."]
    pal = {"h": YEL if lit else YEL_D, "f": face, "j": YEL if lit else YEL_D, "a": YEL_D,
           "b": YEL_L if lit else YEL_D, "k": "#26283a", "m": "#5a5e72", "w": "#4a4e62", "r": "#3a3d50", "e": "#2e3142"}
    p.sprite(x, y, rows, pal, scale)
    p.rect(x + 3 * scale, y + 6 * scale, 3 * scale, scale, "#c8402a" if lit else "#6a2a20")   # 箱子上的一道平台标


def streetlight(p: Pix, x, top, bottom, on=True, reach=46, arm=8):
    p.vline(x, top, bottom, "#3a3f50"); p.vline(x + 1, top, bottom, "#2a2e3c")
    p.hline(x, x + arm, top, "#3a3f50")
    lx = x + arm - 2
    if not on:
        p.rect(lx, top + 1, 5, 2, "#2a2c34")
        return
    p.rect(lx, top + 1, 5, 2, "#ffe7a0")
    cx = lx + 2.5
    for y in range(top + 3, min(bottom, top + 3 + reach)):
        t = (y - top - 3) / reach
        half = 3 + 16 * t
        for xx in range(int(cx - half) - 1, int(cx + half) + 1):
            d = abs(xx + 0.5 - cx) / half
            if d <= 1:
                inten = (1 - d) ** 0.8 * (0.7 - 0.45 * t)
                p.shade(xx, y, inten, "#4a4030" if inten > 0.45 else "#2e2c30")


def city(p: Pix, seed, ground=44):
    """远处的楼和零星亮着的窗。"""
    random.seed(seed)
    x = 0
    while x < 160:
        w, h = random.randint(10, 22), random.randint(14, 34)
        c = random.choice(["#0f1322", "#121729", "#0d1120"])
        p.rect(x, ground - h, w, h, c)
        for wy in range(ground - h + 3, ground - 3, 4):
            for wx in range(x + 2, x + w - 2, 4):
                if random.random() < 0.18:
                    p.rect(wx, wy, 2, 2, random.choice(["#c9a640", "#8a7a4a", "#6a8ab0"]))
        x += w


def crossing() -> Pix:
    """镜头 1：晚上 9:40，解放路口。对面便利店亮着灯，路口这盏路灯是坏的。"""
    p = Pix(160, 90, "#080b16")
    for y, c in [(10, "#0a0e1c"), (22, "#0d1224")]:
        p.rect(0, y, 160, 44 - y, c)
    city(p, 5)
    # 人行道 + 马路
    p.rect(0, 44, 160, 46, "#1e2029")
    for y in range(44, 90):
        l, r = road_x(y)
        p.hline(max(0, int(l)), min(159, int(r)), y, ROAD)
        if y % 6 < 3:                                        # 中线的虚线
            m = 80
            p.hline(m - 1 if y > 70 else m, m, y, "#8a7a40")
    # 横穿马路的路口（横向的一条路）
    p.rect(0, 52, 160, 7, ROAD)
    # 斑马线：在近处横过大路
    for y in range(64, 71):
        l, r = road_x(y)
        x = int(l) + 2
        while x < r - 3:
            p.hline(x, x + 3, y, WALK if y not in (64, 70) else "#9a9a90")
            x += 7
    # 便利店（左，马路对面）：亮着的玻璃门窗、红色招牌「24」
    p.rect(2, 22, 40, 30, "#2a2e3e")
    p.rect(2, 22, 40, 6, "#b02a2a"); p.text(16, 23, "24", "#ffe8a0")
    p.rect(5, 31, 22, 18, "#cfe3c8"); p.rect(29, 31, 10, 21, "#e8f4e0")
    for k in range(3):
        p.hline(6, 26, 35 + k * 5, "#8fae94")                # 货架
    p.vline(16, 31, 48, "#2a2e3e")
    for y in range(52, 62):                                  # 门口地上的一片光
        for x in range(0, 50):
            d = ((x - 30) ** 2 / 500 + (y - 52) ** 2 / 60) ** 0.5
            if d < 1:
                p.shade(x, y, (1 - d) * 0.6, "#3a4038")
    # 路灯：远处一盏亮的；路口这盏坏了
    streetlight(p, 128, 18, 58, on=True, reach=38)
    streetlight(p, 56, 20, 52, on=False)
    p.px(62, 23, "#4a4a3a")                                   # 坏灯的灯罩
    # 斑马线边上：一位提着菜篮的老人
    p.sprite(96, 54, [".gg.", ".ss.", "cccc", "cccc", "cccc", ".cc.", ".l.l"],
             {"g": "#b8b8b8", "s": "#c4946c", "c": "#5a3a4a", "l": "#2a2a30"})
    p.rect(100, 58, 2, 2, "#8a6a3a")
    # 远处驶来的车灯
    p.rect(73, 46, 1, 1, "#fff2c0"); p.rect(77, 46, 1, 1, "#fff2c0")
    return p


def fall() -> Pix:
    """镜头 2：老人倒在斑马线上，菜撒了一地；一辆电动车拖着黄影骑走了。"""
    random.seed(9)
    p = Pix(160, 90, ROAD)
    p.rect(0, 0, 160, 18, "#0b0e18")
    city(p, 12, ground=18)
    p.rect(0, 18, 160, 4, "#1e2029")
    # 近景的斑马线（大透视）
    for y in range(26, 90):
        w = 6 + (y - 26) * 0.18
        gap = 7 + (y - 26) * 0.2
        x = -10 + (y - 26) * -0.3
        while x < 160:
            p.hline(int(x), int(x + w), y, "#d8d8cc" if y % 9 else "#b8b8ae")
            x += w + gap
    for _ in range(300):                                     # 路面的颗粒
        x, y = random.randrange(160), random.randrange(22, 90)
        if p.g[y][x] == ROAD:
            p.px(x, y, ROAD_L)
    # 倒地的老人：侧躺，灰白头发，深色外套
    old = ["......................gg..",
           "..............cccccccsgg..",
           "......pppcccccccccccccss..",
           "....pppppcccccccccccccc...",
           "..sspppp..cccccccc..cc....",
           ".........................."]
    p.sprite(26, 56, old, {"g": "#c8c8c8", "s": "#c4946c", "c": "#5a3a4a", "p": "#2a2a36"}, scale=2)
    # 拐杖、翻倒的菜篮、滚出来的橘子
    for k in range(14):
        p.px(18 + k, 74 - k // 3, "#8a6a3a")
    p.rect(70, 66, 10, 6, "#8a6a3a"); p.hline(70, 79, 66, "#a8844a")
    for (x, y) in [(84, 70), (90, 74), (97, 68), (104, 76), (88, 79)]:
        p.disc(x, y, 1, "#ff9a2a"); p.px(x, y - 1, "#ffc060")
    # 电动车：往右骑出画面，身后拖着黄色的残影
    for y in range(34, 56):
        for x in range(84, 124):
            t = (x - 84) / 40
            if 38 <= y <= 52:
                p.shade(x, y, t * 0.55 * (1 - abs(y - 45) / 9), YEL_D)
    for k in range(10):
        y = 36 + k * 2
        p.hline(92 + k % 3 * 3, 122 - k % 2 * 4, y, "#3a3020")
    rider(p, 120, 32, scale=2, face="#1a120c")
    p.rect(122, 50, 2, 3, "#ff2a2a")                          # 车尾灯
    for x in range(100, 122):
        if x % 2 == 0:
            p.px(x, 51, "#7a1a1a")
    return p


def witness() -> Pix:
    """镜头 3：从便利店里望出去。店员站在门口，马路对面只有一个模糊的黄影。"""
    random.seed(4)
    p = Pix(160, 90, "#cfe3c8")
    # 店里：浅色墙、地砖
    p.rect(0, 60, 160, 30, "#b8c8b0")
    for x in range(0, 160, 10):
        p.vline(x, 60, 89, "#a8b8a0")
    p.hline(0, 159, 60, "#98a890")
    # 玻璃门外：夜里的马路（从门框里看出去）
    gx, gy, gw, gh = 44, 8, 76, 58
    p.rect(gx, gy, gw, gh, "#0a0e1a")
    p.rect(gx, gy + 30, gw, gh - 30, "#14161e")
    p.rect(gx, gy + 34, gw, 8, "#1a1c24")
    for x in range(gx, gx + gw):                             # 远处一排暗的楼
        h = 12 + (x * 7 % 11)
        p.vline(x, gy + 30 - h, gy + 29, "#0e1222")
    for x in range(gx + 3, gx + gw - 3, 6):                  # 斑马线
        p.hline(x, x + 3, gy + 44, "#4a4a46")
    # 对面路灯是坏的，远处一盏亮着
    p.vline(gx + 14, gy + 10, gy + 36, "#22252e"); p.hline(gx + 14, gx + 20, gy + 10, "#22252e")
    p.vline(gx + 66, gy + 14, gy + 36, "#2a2d36"); p.rect(gx + 60, gy + 14, 6, 2, "#ffe7a0")
    for y in range(gy + 16, gy + 38):
        for x in range(gx + 52, gx + gw):
            d = abs(x - (gx + 63)) / (2 + (y - gy - 16) * 0.6)
            if d < 1:
                p.shade(x, y, (1 - d) * 0.35, "#2e2c30")
    # 马路对面：一团看不清的黄影（30 米外）
    for _ in range(90):
        x, y = int(random.gauss(gx + 34, 3.2)), int(random.gauss(gy + 36, 2.6))
        if gx <= x < gx + gw and gy <= y < gy + gh:
            p.px(x, y, random.choice([YEL, YEL_D, YEL_D, "#5a4818"]))
    # 玻璃上的反光（两道斜线）+ 门框 + 门把手 + 贴纸
    for k in range(40):
        for d in (0, 1, 9):
            x, y = gx + 6 + k + d, gy + 50 - k
            if gx <= x < gx + gw and gy <= y < gy + gh and k % 3 != 2:
                p.shade(x, y, 0.35, "#3a4658")
    p.rect(gx - 4, gy - 4, gw + 8, 4, "#6a7280"); p.rect(gx - 4, gy + gh, gw + 8, 3, "#6a7280")
    p.rect(gx - 4, gy, 4, gh, "#6a7280"); p.rect(gx + gw, gy, 4, gh, "#6a7280")
    p.vline(gx + gw // 2, gy, gy + gh - 1, "#7a8290")
    p.rect(gx + gw // 2 - 4, gy + 20, 2, 6, "#c0c6d0"); p.rect(gx + gw // 2 + 3, gy + 20, 2, 8, "#c0c6d0")
    p.rect(gx + 4, gy + 4, 12, 5, "#b02a2a"); p.text(gx + 6, gy + 4, "24", "#ffe8a0")
    # 两边货架
    for x0 in (0, 128):
        p.rect(x0, 10, 32, 50, "#e8eee2"); p.rect(x0, 10, 32, 2, "#98a890")
        for y in (22, 34, 46, 58):
            p.rect(x0, y, 32, 2, "#98a890")
            for k in range(6):
                p.rect(x0 + 2 + k * 5, y - 7, 4, 7, random.choice(["#c84a3a", "#3a7ac8", "#e0b030", "#4aa060", "#e07a30"]))
    # 店员的背影：门口，指着外面
    clerk = ["....hhhhh.....",
             "...hhhhhhh....",
             "...hhhhhhh....",
             "....hhhhh.....",
             ".....nnn......",
             "..uuuuuuuuu...",
             ".uuuuuuuuuuuu.",
             "uuuuuuuuuuuuaa",
             "uuuuuuuuuuuu.a",
             "uuuuuuuuuuuu..",
             "uuuuuuuuuuuu..",
             "uuuuuuuuuuuu.."]
    p.sprite(56, 54, clerk, {"h": "#2a1a12", "n": "#b08060", "u": "#3a8a5a", "a": "#b08060"}, scale=3)
    p.rect(62, 75, 24, 2, "#2e6e48")                          # 围裙带子
    p.rect(98, 75, 3, 3, "#b08060")                           # 伸出去的手指
    return p


def riders() -> Pix:
    """镜头 4：同一条街，一样的黄衣服、黄头盔、黄箱子，十几个。"""
    random.seed(21)
    p = Pix(160, 90, "#080b16")
    p.rect(0, 14, 160, 30, "#0b0f1e")
    city(p, 33, ground=40)
    p.rect(0, 40, 160, 50, ROAD)
    p.rect(0, 40, 160, 3, "#1e2029")
    for x in range(0, 160, 16):
        p.hline(x, x + 7, 66, "#5a5030")
    streetlight(p, 72, 6, 40, on=True, reach=34, arm=10)
    # 三排骑手：远的小、近的大，都朝右，看不出谁是谁
    for (x, y) in [(0, 26), (24, 27), (46, 25), (92, 26), (114, 27), (138, 25)]:
        rider(p, x, y, scale=1, lit=False)
    for (x, y) in [(-6, 46), (34, 48), (76, 46), (118, 48)]:
        rider(p, x, y, scale=2)
    return p


def defendant() -> Pix:
    """镜头 5：法庭。被告席上换成了穿黄色骑手服的陈小军。"""
    p = courtroom()
    swap = {"#1f3a6e": YEL_D}
    for y in range(40, 75):
        for x in range(30, 90):
            if p.g[y][x] in swap:
                p.g[y][x] = swap[p.g[y][x]]
    p.rect(46, 60, 4, 2, "#c8402a")                            # 肩上的平台标，替掉保安徽章
    p.rect(40, 70, 40, 4, "#6a4428")
    # 脚边放着他的黄头盔
    p.sprite(80, 78, ["..hhhh..", ".hhhhhh.", "hhhhhhhh", "hvvvvvhh", "hhhhhhhh"], {"h": YEL, "v": "#2a1c14"}, scale=2)
    return p


SHOTS = [crossing, fall, witness, riders, defendant]

if __name__ == "__main__":
    for i, f in enumerate(SHOTS, 1):
        p = f()
        p.save(OUT / f"case2_{i}.json")
        print(f"case2_{i}.json", len((OUT / f"case2_{i}.json").read_text()), "bytes", len(p.encode()["pal"]), "colors")
