"""开场镜头的像素画工具：160×90 的格子，输出调色板 + 行程编码（页面用 canvas 画、CSS 放大）。

画法见 case1.py。生成：python -m twelve.art.case1  → twelve/art/case1_*.json
"""
from __future__ import annotations

import json

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 62 个字符
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]

# 3×5 像素字体（时间戳、REC 之类）
FONT = {
    "0": ["###", "#.#", "#.#", "#.#", "###"], "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"], "3": ["###", "..#", "###", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"], "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"], "7": ["###", "..#", "..#", "..#", "..#"],
    "8": ["###", "#.#", "###", "#.#", "###"], "9": ["###", "#.#", "###", "..#", "###"],
    ":": [".", "#", ".", "#", "."], "-": ["...", "...", "###", "...", "..."], " ": ["..", "..", "..", "..", ".."],
    "A": [".#.", "#.#", "###", "#.#", "#.#"], "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": ["###", "#..", "#..", "#..", "###"], "E": ["###", "#..", "##.", "#..", "###"],
    "M": ["#.#", "###", "###", "#.#", "#.#"], "R": ["##.", "#.#", "##.", "#.#", "#.#"],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"], "O": ["###", "#.#", "#.#", "#.#", "###"],
    "N": ["#..#", "##.#", "#.##", "#..#", "#..#"], "Z": ["###", "..#", ".#.", "#..", "###"],
}


class Pix:
    def __init__(self, w=160, h=90, bg="#000000"):
        self.w, self.h = w, h
        self.g = [[bg] * w for _ in range(h)]

    def px(self, x, y, c):
        if c and 0 <= x < self.w and 0 <= y < self.h:
            self.g[y][x] = c

    def rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.px(xx, yy, c)

    def hline(self, x0, x1, y, c):
        for x in range(x0, x1 + 1):
            self.px(x, y, c)

    def vline(self, x, y0, y1, c):
        for y in range(y0, y1 + 1):
            self.px(x, y, c)

    def dither(self, x, y, w, h, c, phase=0):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if (xx + yy + phase) % 2 == 0:
                    self.px(xx, yy, c)

    def shade(self, x, y, inten, c):
        """按 0..1 的强度用 4×4 有序抖动决定这一格要不要上色（做渐变、光、模糊）。"""
        if inten > (BAYER[y % 4][x % 4] + 0.5) / 16:
            self.px(x, y, c)

    def text(self, x, y, s, c):
        for ch in s:
            glyph = FONT[ch]
            for r, row in enumerate(glyph):
                for k, v in enumerate(row):
                    if v == "#":
                        self.px(x + k, y + r, c)
            x += len(glyph[0]) + 1

    def sprite(self, x, y, rows, pal, scale=1):
        """rows：字符画；pal：字符 -> 颜色（'.' 透明）；scale：每格放大几倍。"""
        for r, row in enumerate(rows):
            for k, ch in enumerate(row):
                if ch != "." and ch in pal:
                    self.rect(x + k * scale, y + r * scale, scale, scale, pal[ch])

    def disc(self, cx, cy, r, c):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r:
                    self.px(x, y, c)

    def encode(self) -> dict:
        pal, idx = [], {}
        for row in self.g:
            for c in row:
                if c not in idx:
                    idx[c] = len(pal)
                    pal.append(c)
        assert len(pal) <= len(ALPHABET), "颜色太多"
        out, flat = [], [c for row in self.g for c in row]
        i = 0
        while i < len(flat):
            c, n = flat[i], 1
            while i + n < len(flat) and flat[i + n] == c and n < len(ALPHABET):
                n += 1
            out.append(ALPHABET[idx[c]] + ALPHABET[n - 1])
            i += n
        return {"w": self.w, "h": self.h, "pal": pal, "rle": "".join(out)}

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.encode(), f, separators=(",", ":"))
