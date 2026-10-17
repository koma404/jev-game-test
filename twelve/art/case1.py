"""第 1 关《夜班》开场的 4 个镜头。python -m twelve.art.case1 生成 twelve/art/case1_1..4.json"""
from __future__ import annotations

import random
from pathlib import Path

from .pix import Pix

OUT = Path(__file__).parent


def warehouse() -> Pix:
    """镜头 1：凌晨 2:14，物流仓库 B 区。"""
    random.seed(7)
    p = Pix(160, 90, "#070a14")
    for y, c in [(14, "#0a0e1c"), (26, "#0d1224"), (36, "#10162b")]:
        p.rect(0, y, 160, 90 - y, c)
    for y, c in [(12, "#0a0e1c"), (24, "#0d1224"), (34, "#10162b")]:
        p.dither(0, y, 160, 2, c)
    for _ in range(28):
        p.px(random.randrange(160), random.randrange(34), random.choice(["#3a4060", "#565c80", "#8a90b0"]))
    # 月亮
    p.rect(132, 8, 8, 8, "#d8d4b8"); p.rect(131, 9, 10, 6, "#d8d4b8"); p.rect(133, 7, 6, 10, "#d8d4b8")
    p.rect(135, 7, 6, 7, "#0a0e1c"); p.rect(136, 8, 5, 7, "#0a0e1c")
    # 仓库：锯齿屋顶 + 墙 + 板缝
    wall, wall_d, roof = "#252b40", "#1d2235", "#30374f"
    p.rect(40, 34, 112, 40, wall)
    for i in range(4):
        x = 40 + i * 28
        for k in range(10):
            p.hline(x, x + k * 2 + 7, 33 - k, roof)
        p.vline(x + 27, 24, 33, wall_d)
    p.rect(40, 34, 112, 2, roof)
    for x in range(42, 152, 6):
        p.vline(x, 37, 73, wall_d)
    # 卷帘门 + 「B」牌子
    p.rect(86, 46, 34, 28, "#141826")
    for y in range(47, 74, 2):
        p.hline(87, 118, y, "#1b2032")
    p.rect(86, 45, 34, 1, "#3a4260")
    p.rect(98, 37, 10, 8, "#0f1220"); p.rect(99, 38, 8, 6, "#c9a640")
    p.sprite(101, 38, ["####", "#..#", "###.", "#..#", "####"], {"#": "#0f1220"})
    # 墙角的监控摄像头，亮着红点
    p.rect(144, 38, 6, 3, "#4a5068"); p.rect(146, 41, 2, 3, "#3a3f55"); p.px(144, 39, "#ff3b3b")
    # 地面
    p.rect(0, 74, 160, 16, "#11131c")
    for x in range(0, 160, 12):
        p.hline(x, x + 5, 82, "#1a1d29")
    # 路灯：从灯头往下、从中间往两边渐淡的光
    p.vline(22, 28, 73, "#3a3f50"); p.vline(23, 28, 73, "#2a2e3c")
    p.hline(22, 30, 28, "#3a3f50"); p.rect(28, 29, 5, 2, "#ffe7a0")
    for y in range(31, 74):
        t = (y - 31) / 43
        half = 3 + 18 * t
        for x in range(int(30 - half) - 1, int(31 + half) + 1):
            d = abs(x + 0.5 - 30.5) / half
            if d <= 1:
                inten = (1 - d) ** 0.8 * (0.75 - 0.45 * t)
                p.shade(x, y, inten, "#4a4030" if inten > 0.45 else "#2e2c30")
    for (x, y) in [(27, 30), (33, 30), (26, 29), (34, 29), (30, 32), (29, 32), (31, 32), (28, 31), (32, 31)]:
        p.px(x, y, "#8a7448")
    for x in range(8, 53):
        d = abs(x - 30)
        if d < 22 and (d < 14 or x % 2 == 0):
            p.px(x, 74, "#6a5632")
        if d < 16 and x % 2 == 1:
            p.px(x, 75, "#4a3d26")
        if d < 9 and x % 2 == 0:
            p.px(x, 76, "#3a3122")
    # 围栏
    for x in range(0, 40, 4):
        p.vline(x, 64, 73, "#1c2030")
    p.hline(0, 39, 66, "#1c2030"); p.hline(0, 39, 70, "#1c2030")
    return p


def cctv() -> Pix:
    """镜头 2：B 区监控画面，一个穿保安制服的身影抱着电缆，看不清脸。"""
    random.seed(3)
    G = ["#06120a", "#0c2414", "#163a22", "#245a34", "#3f8a52", "#79c78c", "#b8f0c4"]
    p = Pix(160, 90, G[1])
    # 地面带透视的线
    p.rect(0, 62, 160, 28, G[2])
    for k in range(-6, 7):
        x0 = 80 + k * 6
        for y in range(62, 90):
            p.px(int(x0 + (x0 - 80) * (y - 62) / 10), y, G[1])
    # 两边的货架 + 一卷卷电缆
    for side in (0, 1):
        x0 = 6 if side == 0 else 122
        for x in (x0, x0 + 30):
            p.vline(x, 14, 66, G[3])
        for y in (22, 38, 54):
            p.hline(x0, x0 + 30, y, G[3])
            for k in range(3):
                cx = x0 + 6 + k * 9
                p.disc(cx, y - 4, 3, G[4]); p.disc(cx, y - 4, 1, G[2])
    # 人影：先画一个往左错开的"残影"（模糊、在动），再画本体；脸只有一团
    body = [".....kkkkk......", "....kkkkkkk.....", "...kkkkkkkkkk...", ".....fffff......",
            ".....fffff......", ".....fffff......", "......fff.......", "...bbbbbbbbb....",
            "..bbbbbbbbbbb...", ".bbbbooooobbbb..", ".bbboo...oobbb..", ".bbbo.....obbb..",
            ".bbbo.....obbb..", ".bbbo.....obbb..", ".bbboo...oobbb..", "..bbbooooobbb...",
            "..bbbbbbbbbbb...", "..bbbbbbbbbbb...", "...bbbbbbbbb....", "...ddddddddd....",
            "...ddd...ddd....", "...ddd...ddd....", "...ddd...ddd....", "...ddd...ddd....",
            "...ddd...ddd....", "...ddd...ddd....", "..dddd...dddd..."]
    ghost = {ch: G[2] for ch in "kfbdo"}
    p.sprite(60, 22, body, ghost, scale=2)
    p.sprite(64, 22, body, {"k": G[4], "f": G[3], "b": G[4], "d": G[3], "o": G[5]}, scale=2)
    p.hline(64 + 4, 64 + 24, 22 + 5, G[5])                       # 帽檐的高光：一眼看出是制服帽
    p.rect(64 + 14, 22 + 18, 4, 2, G[6])                          # 胸前的徽章反光
    for _ in range(260):                         # 人影边缘的噪点，脸糊成一团
        x, y = random.randrange(60, 98), random.randrange(20, 50)
        p.px(x, y, random.choice([G[2], G[3], G[4]]))
    # 雪花噪点 + 扫描线 + 一条错位带
    for _ in range(260):
        p.px(random.randrange(160), random.randrange(90), random.choice([G[3], G[4], G[0]]))
    for y in range(0, 90, 3):
        for x in range(160):
            if p.g[y][x] in (G[1], G[2]):
                p.px(x, y, G[0])
    band = [row[:] for row in p.g[46:50]]
    for i, row in enumerate(band):
        p.g[46 + i] = row[4:] + row[:4]
    # 取景框角标、REC、时间戳、机位
    for (x, y, dx, dy) in [(3, 3, 1, 1), (156, 3, -1, 1), (3, 86, 1, -1), (156, 86, -1, -1)]:
        p.hline(min(x, x + 5 * dx), max(x, x + 5 * dx), y, G[6]); p.vline(x, min(y, y + 5 * dy), max(y, y + 5 * dy), G[6])
    p.rect(9, 8, 3, 3, "#ff3030"); p.text(14, 7, "REC", G[6])
    p.text(118, 7, "02:14:07", G[6])
    p.text(9, 78, "CAM B-03", G[5])
    return p


def time_clock() -> Pix:
    """镜头 3：2:10，A 区的打卡机亮了一下。"""
    p = Pix(160, 90, "#151a26")
    # 走廊墙面：上下两色 + 踢脚线
    p.rect(0, 0, 160, 58, "#1c2232"); p.rect(0, 58, 160, 32, "#141822")
    for x in range(0, 160, 20):
        p.vline(x, 0, 57, "#181d2b")
    p.rect(0, 56, 160, 2, "#2a3146")
    # 墙上的「A」区牌
    p.rect(10, 10, 32, 30, "#c9a640"); p.rect(11, 11, 30, 28, "#e0bd52")
    p.sprite(20, 12, [".#.", "#.#", "###", "#.#", "#.#"], {"#": "#1c2232"}, scale=4)   # 大写 A
    p.text(13, 33, "A-ZONE", "#1c2232")
    # 打卡机
    p.rect(66, 18, 34, 46, "#8a93a8"); p.rect(67, 19, 32, 44, "#a3acc0"); p.rect(66, 63, 34, 2, "#5a6276")
    p.rect(71, 23, 24, 12, "#0b0f12"); p.rect(72, 24, 22, 10, "#0d1a12")
    p.text(74, 26, "02:10", "#58f08a")
    p.text(74, 26 + 6, "", "#58f08a")
    p.rect(74, 40, 18, 14, "#6c7488"); p.rect(75, 41, 16, 12, "#7a8296")      # 刷卡区
    for k in range(3):
        p.hline(78, 88, 44 + k * 3, "#9aa2b6")
    p.rect(92, 57, 3, 3, "#3cff7a"); p.rect(91, 56, 5, 1, "#1d6b38")         # 绿灯
    # 光晕：屏幕和绿灯照亮一点墙
    for y in range(14, 70):
        for x in range(56, 112):
            d = ((x - 83) ** 2 / 900 + (y - 40) ** 2 / 500) ** 0.5
            if d < 1 and p.g[y][x] in ("#1c2232", "#141822", "#181d2b"):
                p.shade(x, y, (1 - d) * 0.35, "#22303a")
    # 伸过来刷卡的手（握着工牌贴在刷卡区）+ 保安制服的袖子
    hand = ["......ssssssss", "....ssssssssss", "..ccccccccsssss", "..cwwwwwwcssss.", "..cwbbbbwcssss.",
            "..cwbppbwcsss..", "..cwbbbbwc.....", "..cwwwwwwc.....", "..cccccccc....."]
    p.sprite(73, 42, hand, {"s": "#b07d5a", "c": "#d8dce6", "w": "#f2f4f8", "b": "#3c6fd0", "p": "#2a3b66"})
    p.rect(87, 41, 73, 10, "#1f3a6e"); p.rect(87, 42, 73, 8, "#2a4a86"); p.rect(87, 41, 3, 10, "#16294f")
    p.hline(87, 159, 45, "#22407a")
    # 屏幕底下一行小字：张伟的工号
    p.text(74, 32, "0817", "#2fa25a")
    return p


def courtroom() -> Pix:
    """镜头 4：法庭。张伟低着头坐在被告席，右边是十二位陪审员。"""
    random.seed(11)
    p = Pix(160, 90, "#2a1a12")
    # 墙：木板 + 上方的天平徽记
    p.rect(0, 0, 160, 50, "#3a2418")
    for x in range(0, 160, 10):
        p.vline(x, 0, 49, "#33201a")
    p.hline(0, 159, 49, "#4a3020")
    p.hline(70, 89, 3, "#c9a640"); p.vline(79, 1, 8, "#c9a640"); p.vline(80, 1, 8, "#c9a640")
    p.vline(71, 3, 5, "#8a7030"); p.vline(88, 3, 5, "#8a7030")
    p.hline(68, 74, 6, "#c9a640"); p.hline(85, 91, 6, "#c9a640"); p.hline(77, 82, 9, "#c9a640")
    # 法官席 + 法官
    p.rect(52, 22, 56, 16, "#5a3a24"); p.rect(52, 22, 56, 2, "#7a5232"); p.rect(52, 36, 56, 2, "#3e2818")
    p.rect(75, 15, 10, 7, "#141010"); p.rect(77, 11, 6, 5, "#c4946c"); p.rect(77, 10, 6, 2, "#d8d8d8")
    # 地板
    p.rect(0, 50, 160, 40, "#24170f")
    for y in range(52, 90, 4):
        p.hline(0, 159, y, "#2c1c12")
    # 陪审席（右）：两排各 6 个人，只画剪影
    p.rect(110, 40, 48, 30, "#4a2e1c"); p.rect(110, 40, 48, 2, "#6a4428")
    colors = ["#2b2b38", "#3a3040", "#2e3a34", "#40342a", "#2a3444", "#38302e"]
    for row in range(2):
        for k in range(6):
            x, y = 113 + k * 7 + (3 if row else 0), 30 + row * 10
            if x > 154:
                continue
            p.rect(x, y + 4, 6, 6, colors[(k + row) % 6]); p.rect(x + 1, y, 4, 4, "#1e1612")
    p.rect(108, 52, 52, 18, "#4a2e1c"); p.rect(108, 52, 52, 2, "#6a4428")
    # 被告席上的聚光
    for y in range(18, 90):
        for x in range(30, 92):
            d = ((x - 60) ** 2 / (220 + (y - 18) * 9) + 0) ** 0.5
            if d < 1:
                p.shade(x, y, (1 - d) * 0.28 * (0.6 + (y - 18) / 120), "#4a3624")
    # 被告桌 + 张伟（背影、低头、保安制服）
    p.rect(40, 70, 40, 4, "#6a4428"); p.rect(42, 74, 2, 16, "#4a2e1c"); p.rect(76, 74, 2, 16, "#4a2e1c")
    zw = ["......hhhhhh......", ".....hhhhhhhh.....", ".....hhhhhhhh.....", "......hhhhhh......",
          ".......nnnn.......", "..uuuuunnnnuuuuu..", ".uuuuuuuuuuuuuuuu.", "uuuuuuuuuuuuuuuuuu",
          "uuuuuuuuuuuuuuuuuu", "uuuuuuuuuuuuuuuuuu", "uuuuuuuuuuuuuuuuuu"]
    p.sprite(42, 48, zw, {"h": "#1a1210", "n": "#8a5f45", "u": "#1f3a6e"}, scale=2)   # 低着头：后颈露出来
    p.rect(46, 60, 4, 2, "#c9a640")                     # 肩上的保安徽章
    p.rect(40, 70, 40, 4, "#6a4428")                    # 桌面压在身前
    return p


SHOTS = [warehouse, cctv, time_clock, courtroom]

if __name__ == "__main__":
    for i, f in enumerate(SHOTS, 1):
        p = f()
        p.save(OUT / f"case1_{i}.json")
        print(f"case1_{i}.json", len((OUT / f"case1_{i}.json").read_text()), "bytes", len(p.encode()["pal"]), "colors")
