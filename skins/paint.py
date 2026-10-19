"""Paint the companions' 64x64 Minecraft skins (classic layout) and write them for body.js (raw RGBA, base64)
plus a PNG preview of each.   python3 skins/paint.py"""
import base64
import os
import struct
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def hexc(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [255]


def box(img, x, y, w, h, d, col):
    """Fill every face of a cuboid part laid out the classic way: (x,y) is the top-left of its texture block."""
    img[y:y + d, x + d:x + d + 2 * w] = col          # top + bottom
    img[y + d:y + d + h, x:x + 2 * d + 2 * w] = col  # right, front, left, back


def faces(x, y, w, h, d):
    """Rectangles (x0, y0, width, height) of each face of a cuboid part."""
    return {"top": (x + d, y, w, d), "bottom": (x + d + w, y, w, d), "right": (x, y + d, d, h),
            "front": (x + d, y + d, w, h), "left": (x + d + w, y + d, d, h), "back": (x + 2 * d + w, y + d, w, h)}


def paint(img, rect, col, rows=None):
    x, y, w, h = rect
    r0, r1 = rows or (0, h)
    img[y + r0:y + r1, x:x + w] = col


def person(skin, hair, eyes, shirt, trousers, shoes, slim, long_hair, extra=None):
    img = np.zeros((64, 64, 4), np.uint8)
    aw = 3 if slim else 4
    head = faces(0, 0, 8, 8, 8)
    box(img, 0, 0, 8, 8, 8, skin)
    # hair: top, back, sides (down to the ears, or to the jaw for long hair), a fringe at the front
    paint(img, head["top"], hair)
    paint(img, head["back"], hair)
    for side in ("left", "right"):
        paint(img, head[side], hair, (0, 8 if long_hair else 3))
    paint(img, head["front"], hair, (0, 2))
    fx, fy = head["front"][:2]
    img[fy + 2, fx] = hair
    img[fy + 2, fx + 7] = hair
    if long_hair:
        img[fy + 2:fy + 8, fx] = hair
        img[fy + 2:fy + 8, fx + 7] = hair
    # face
    for ex in (1, 5):
        img[fy + 4, fx + ex] = hexc("#ffffff")
        img[fy + 4, fx + ex + 1] = eyes
    img[fy + 3, fx + 1:fx + 3] = hair if not long_hair else skin
    img[fy + 3, fx + 5:fx + 7] = hair if not long_hair else skin
    img[fy + 6, fx + 3:fx + 5] = hexc("#b0504a")
    # body and arms in the shirt, hands showing
    box(img, 16, 16, 8, 12, 4, shirt)
    for (x, y) in ((40, 16), (32, 48)):
        box(img, x, y, aw, 12, 4, shirt)
        for f in faces(x, y, aw, 12, 4).values():
            if f[3] == 12:
                paint(img, f, skin, (8, 12))
        paint(img, faces(x, y, aw, 12, 4)["bottom"], skin)
    # legs
    for (x, y) in ((0, 16), (16, 48)):
        box(img, x, y, 4, 12, 4, trousers)
        for f in faces(x, y, 4, 12, 4).values():
            if f[3] == 12:
                paint(img, f, shoes, (10, 12))
        paint(img, faces(x, y, 4, 12, 4)["bottom"], shoes)
    if long_hair:
        # hair falling down the back, on the jacket layer
        jb = faces(16, 32, 8, 12, 4)["back"]
        paint(img, jb, hair, (0, 5))
        # and the hat layer, so it frames the face a little
        hat = faces(32, 0, 8, 8, 8)
        paint(img, hat["back"], hair)
        for side in ("left", "right"):
            paint(img, hat[side], hair, (0, 8))
    if extra:
        extra(img, faces)
    return img


def claude_extra(img, faces):
    # a little white star on the hoodie
    fx, fy = faces(16, 16, 8, 12, 4)["front"][:2]
    for dx, dy in ((3, 4), (4, 4), (3, 3), (4, 5), (2, 4), (5, 4)):
        img[fy + dy, fx + dx] = hexc("#fff3e0")


def claudia_extra(img, faces):
    # a skirt: the top of the legs in the dress colour, a belt
    for (x, y) in ((0, 16), (16, 48)):
        for f in faces(x, y, 4, 12, 4).values():
            if f[3] == 12:
                paint(img, f, hexc("#7b4fc9"), (0, 4))
    fx, fy = faces(16, 16, 8, 12, 4)["front"][:2]
    img[fy + 8, fx:fx + 8] = hexc("#f2c94c")


def gaston_extra(img, faces):
    # a black beret (top of the head, a band round it), a curly moustache, a striped shirt and a red scarf
    beret = hexc("#1c1c24")
    hat = faces(32, 0, 8, 8, 8)
    paint(img, hat["top"], beret)
    for side in ("front", "back", "left", "right"):
        paint(img, hat[side], beret, (0, 2))
    img[hat["front"][1] + 2, hat["front"][0] + 5:hat["front"][0] + 8] = beret  # the tilt
    fx, fy = faces(0, 0, 8, 8, 8)["front"][:2]
    stache = hexc("#2b1a10")
    img[fy + 6, fx + 2:fx + 6] = stache
    img[fy + 5, fx + 1] = stache
    img[fy + 5, fx + 6] = stache
    img[fy + 6, fx + 3:fx + 5] = stache
    img[fy + 7, fx + 3:fx + 5] = hexc("#b0504a")
    # stripes on the shirt and sleeves, every other row
    for (x, y, w) in ((16, 16, 8), (40, 16, 4), (32, 48, 4)):
        for f in faces(x, y, w, 12, 4).values():
            if f[3] == 12:
                for r in range(0, 8, 2):
                    paint(img, f, hexc("#2a3f8f"), (r, r + 1))
    bx, by = faces(16, 16, 8, 12, 4)["front"][:2]
    img[by:by + 2, bx:bx + 8] = hexc("#c8202f")
    img[by + 2:by + 5, bx + 5:bx + 7] = hexc("#c8202f")


def dash_extra(img, faces):
    # a backwards cap, a headset, a lightning bolt on the hoodie, stripes down the joggers
    cap = hexc("#1f8a4c")
    hat = faces(32, 0, 8, 8, 8)
    paint(img, hat["top"], cap)
    for side in ("front", "back", "left", "right"):
        paint(img, hat[side], cap, (0, 2))
    bx, by = hat["back"][:2]
    img[by + 2:by + 3, bx + 2:bx + 6] = cap  # the peak, at the back
    head = faces(0, 0, 8, 8, 8)
    for side in ("left", "right"):
        x, y = head[side][:2]
        img[y + 3:y + 6, x + 3:x + 5] = hexc("#222222")  # headset ear cups
    fx, fy = faces(16, 16, 8, 12, 4)["front"][:2]
    for dx, dy in ((4, 2), (3, 3), (4, 3), (2, 4), (3, 4), (4, 4), (5, 4), (4, 5), (3, 5), (3, 6), (2, 7)):
        img[fy + dy, fx + dx] = hexc("#f6d33c")
    for (x, y) in ((0, 16), (16, 48)):
        f = faces(x, y, 4, 12, 4)["right"]
        paint(img, f, hexc("#f4f4f4"), (0, 10))


def costume(hat=None, hat_band=None, visor=None, cape=None, wings=None, goggles=None, glasses=None, patch=False,
            belt=None, emblem=None, coat=None, apron=None):
    """Extras for a costume, drawn on the hat/jacket layers so it reads from a distance."""
    def extra(img, faces):
        hat_l = faces(32, 0, 8, 8, 8)
        head = faces(0, 0, 8, 8, 8)
        fx, fy = head["front"][:2]
        if hat:
            paint(img, hat_l["top"], hexc(hat))
            for side in ("front", "back", "left", "right"):
                paint(img, hat_l[side], hexc(hat), (0, 2 if not visor else 8))
            if hat_band:
                for side in ("front", "back", "left", "right"):
                    paint(img, hat_l[side], hexc(hat_band), (2, 3))
        if visor:  # a helmet: the face shows through a tinted window
            hx, hy = hat_l["front"][:2]
            img[hy + 2:hy + 7, hx + 1:hx + 7] = 0
            img[hy + 2:hy + 7, hx + 1:hx + 7] = hexc(visor)[:3] + [90]
        if goggles:
            img[fy + 3, fx + 1:fx + 7] = hexc(goggles)
            img[fy + 4, fx + 1:fx + 3] = hexc("#9fd3ff")
            img[fy + 4, fx + 5:fx + 7] = hexc("#9fd3ff")
        if glasses:
            for ex in (1, 5):
                img[fy + 4, fx + ex] = hexc(glasses)
                img[fy + 4, fx + ex + 1] = hexc(glasses)
        if patch:
            img[fy + 4, fx + 5:fx + 7] = hexc("#111111")
            img[fy + 3, fx:fx + 8] = hexc("#111111")
        body = faces(16, 16, 8, 12, 4)
        bx, by = body["front"][:2]
        if belt:
            img[by + 7, bx:bx + 8] = hexc(belt)
        if emblem:
            img[by + 2:by + 4, bx + 3:bx + 5] = hexc(emblem)
        if coat:  # an open coat: the coat colour down both sides of the front, and the back and sleeves
            jacket = faces(16, 32, 8, 12, 4)
            for side in ("back", "left", "right"):
                paint(img, jacket[side], hexc(coat))
            jx, jy = jacket["front"][:2]
            img[jy:jy + 12, jx:jx + 2] = hexc(coat)
            img[jy:jy + 12, jx + 6:jx + 8] = hexc(coat)
            for (x, y) in ((40, 32), (48, 48)):
                for f in faces(x, y, 3, 12, 4).values():
                    if f[3] == 12:
                        paint(img, f, hexc(coat), (0, 9))
        if apron:
            img[by + 5:by + 12, bx + 1:bx + 7] = hexc(apron)
        if cape:
            paint(img, body["back"], hexc(cape))
        if wings:
            bk = body["back"]
            for r in range(1, 8):
                img[bk[1] + r, bk[0] + (r % 2):bk[0] + 3] = hexc(wings)
                img[bk[1] + r, bk[0] + 5:bk[0] + 8 - (r % 2)] = hexc(wings)
    return extra


def girl(skin, hair, eyes, shirt, trousers, shoes, **extra):
    return dict(skin=hexc(skin), hair=hexc(hair), eyes=hexc(eyes), shirt=hexc(shirt), trousers=hexc(trousers),
                shoes=hexc(shoes), slim=True, long_hair=True, extra=costume(**extra))


def baby_extra(img, faces):
    # a tuft of hair, big eyes, a dummy, a nappy over a onesie
    head = faces(0, 0, 8, 8, 8)
    fx, fy = head["front"][:2]
    tx, ty = head["top"][:2]
    img[ty:ty + 8, tx:tx + 8] = hexc("#f3d2b8")
    img[ty + 3:ty + 5, tx + 3:tx + 5] = hexc("#e3b04b")
    img[fy:fy + 2, fx:fx + 8] = hexc("#f3d2b8")
    img[fy, fx + 3:fx + 5] = hexc("#e3b04b")
    for ex in (1, 5):  # big round eyes
        img[fy + 3:fy + 5, fx + ex:fx + ex + 2] = hexc("#ffffff")
        img[fy + 4, fx + ex + (1 if ex == 1 else 0)] = hexc("#3b6fb6")
    img[fy + 6, fx + 3:fx + 5] = hexc("#5ab0f0")  # the dummy
    img[fy + 5, fx + 3:fx + 5] = hexc("#f28ab2")
    for (x, y) in ((0, 16), (16, 48)):  # nappy on the top of the legs, bare below
        for f in faces(x, y, 4, 12, 4).values():
            if f[3] == 12:
                paint(img, f, hexc("#fbfbfb"), (0, 4))
                paint(img, f, hexc("#f3d2b8"), (4, 12))
        paint(img, faces(x, y, 4, 12, 4)["bottom"], hexc("#f3d2b8"))
    bx, by = faces(16, 16, 8, 12, 4)["front"][:2]
    img[by + 9:by + 12, bx:bx + 8] = hexc("#fbfbfb")
    img[by + 3, bx + 3:bx + 5] = hexc("#f6d33c")  # a little duck


def guard(skin, hair, colour, trim):
    """A guard in armour: a helmet with a coloured crest, plate in his colour, a cape."""
    return dict(skin=hexc(skin), hair=hexc(hair), eyes=hexc("#3b3b3b"), shirt=hexc("#a9b0bb"), trousers=hexc("#7d8591"),
                shoes=hexc("#4a4f57"), slim=False, long_hair=False,
                extra=costume(hat="#b8bec9", hat_band=colour, cape=colour, emblem=trim, belt=colour))


def vasilis_extra(img, faces):
    # Greek blue and white: a striped shirt with a white cross on the chest, a blue headband
    bx, by = faces(16, 16, 8, 12, 4)["front"][:2]
    for r in range(0, 12, 3):
        img[by + r, bx:bx + 8] = hexc("#f4f6fb")
    img[by + 1:by + 6, bx + 1:bx + 4] = hexc("#0d5eaf")
    img[by + 3, bx + 1:bx + 4] = hexc("#f4f6fb")
    img[by + 1:by + 6, bx + 2] = hexc("#f4f6fb")
    hat = faces(32, 0, 8, 8, 8)
    for side in ("front", "back", "left", "right"):
        paint(img, hat[side], hexc("#0d5eaf"), (2, 3))


def kostas_extra(img, faces):
    # a grey flat cap, sunglasses, a big moustache, a white short-sleeved shirt with a pen in the pocket
    hat = faces(32, 0, 8, 8, 8)
    paint(img, hat["top"], hexc("#6b6b70"))
    for side in ("front", "back", "left", "right"):
        paint(img, hat[side], hexc("#6b6b70"), (0, 2))
    hx, hy = hat["front"][:2]
    img[hy + 2, hx:hx + 8] = hexc("#55555a")  # the peak
    fx, fy = faces(0, 0, 8, 8, 8)["front"][:2]
    img[fy + 4, fx + 1:fx + 7] = hexc("#111111")  # shades
    img[fy + 6, fx + 1:fx + 7] = hexc("#2b2018")  # moustache
    img[fy + 5, fx + 2:fx + 6] = hexc("#2b2018")
    bx, by = faces(16, 16, 8, 12, 4)["front"][:2]
    img[by + 2:by + 4, bx + 5] = hexc("#2f63c9")  # pen
    img[by + 1:by + 12, bx + 3] = hexc("#dcdcd8")  # shirt buttons line


SKINS = {
    "kostas": dict(skin=hexc("#c99070"), hair=hexc("#3a3a3a"), eyes=hexc("#3b2a1a"), shirt=hexc("#f6f6f2"),
                   trousers=hexc("#2c3140"), shoes=hexc("#2b1a10"), slim=False, long_hair=False, extra=kostas_extra),
    "vasilis": dict(skin=hexc("#c99070"), hair=hexc("#1c140f"), eyes=hexc("#5a3a22"), shirt=hexc("#0d5eaf"),
                    trousers=hexc("#f4f6fb"), shoes=hexc("#3a2a1a"), slim=False, long_hair=False, extra=vasilis_extra),
    # Rex the pro: no armour any more, a hoodie, cool shades and a gamer's lightning badge
    "rex": dict(skin=hexc("#e0ac85"), hair=hexc("#4a2f1d"), eyes=hexc("#2f63c9"), shirt=hexc("#2f63c9"),
                trousers=hexc("#2b2f3a"), shoes=hexc("#f4f4f4"), slim=False, long_hair=False,
                extra=costume(glasses="#111111", emblem="#f2d14b", belt="#1f3f8a")),
    "bolt": guard("#b98463", "#1b1b1b", "#2f9a4c", "#f2d14b"),
    "titan": guard("#f0c8a8", "#c48a3a", "#d4a017", "#c8202f"),
    "baby": dict(skin=hexc("#f3d2b8"), hair=hexc("#f3d2b8"), eyes=hexc("#3b6fb6"), shirt=hexc("#a9d8f5"),
                 trousers=hexc("#fbfbfb"), shoes=hexc("#f3d2b8"), slim=True, long_hair=False, extra=baby_extra),
    "luna": girl("#f2cfb1", "#d9d9e8", "#5b6fd6", "#eef0f5", "#eef0f5", "#9aa3b5", hat="#f7f8fb", visor="#3a5fbf",
                 emblem="#d13b3b", belt="#9aa3b5"),
    "rosie": girl("#f5d0b5", "#e07a3a", "#3f8f5a", "#f28ab2", "#3f8f5a", "#6b4a2b", hat="#e6c86e", hat_band="#d14d7a",
                  apron="#3f8f5a"),
    "ivy": girl("#d9a37e", "#5a3a22", "#4a7a3a", "#c9b27c", "#7a6a4a", "#5a3a22", hat="#8a6a3a", hat_band="#4a3420",
                belt="#4a3420", emblem="#3f8f5a"),
    "mila": girl("#f0c8a8", "#2b2b2b", "#6b4a2b", "#f8f8f8", "#2b2b2b", "#2b2b2b", hat="#ffffff", hat_band="#eeeeee",
                 apron="#ffffff", emblem="#d13b3b"),
    "zara": girl("#c99272", "#1f1f1f", "#6b4a2b", "#b8bec9", "#8a919c", "#5a5f68", hat="#c9ced6", hat_band="#c8202f",
                 cape="#c8202f", emblem="#e8c33a", belt="#5a5f68"),
    "pixie": girl("#f6d8c4", "#e88ad6", "#a05ad1", "#b07ce6", "#f2a6d9", "#f2a6d9", wings="#d6f0ff", emblem="#fff27a"),
    "skye": girl("#e8b48f", "#a0522d", "#3b6fb6", "#7a4a2a", "#3a4a5a", "#2b1a10", hat="#5a3a22", goggles="#c48a3a",
                 coat="#7a4a2a", belt="#2b1a10"),
    "nova": girl("#e0b090", "#7a3fd1", "#3b6fb6", "#3b6fb6", "#2b2b3a", "#2b2b3a", glasses="#222222", coat="#f4f4f4"),
    "juno": girl("#d29a76", "#3a2214", "#6b4a2b", "#f4f1ea", "#22222a", "#2b1a10", hat="#c8202f", patch=True,
                 belt="#2b1a10", emblem="#e8c33a"),
    "dash": dict(skin=hexc("#c68863"), hair=hexc("#1b1b1b"), eyes=hexc("#2f6b3a"), shirt=hexc("#23262e"),
                 trousers=hexc("#3a3f4a"), shoes=hexc("#e8e8e8"), slim=False, long_hair=False, extra=dash_extra),
    "gaston": dict(skin=hexc("#f1c9a5"), hair=hexc("#2b1a10"), eyes=hexc("#4a6b8a"), shirt=hexc("#f4f1ea"),
                   trousers=hexc("#22222a"), shoes=hexc("#5a3a22"), slim=False, long_hair=False, extra=gaston_extra),
    "claude": dict(skin=hexc("#e8b48f"), hair=hexc("#4a2f1d"), eyes=hexc("#3b6fb6"), shirt=hexc("#d97757"),
                   trousers=hexc("#2f4a7a"), shoes=hexc("#3a3a3a"), slim=False, long_hair=False, extra=claude_extra),
    "claudia": dict(skin=hexc("#f0c3a0"), hair=hexc("#a0522d"), eyes=hexc("#3f8f5a"), shirt=hexc("#7b4fc9"),
                    trousers=hexc("#f0c3a0"), shoes=hexc("#c0396b"), slim=True, long_hair=True, extra=claudia_extra),
}


def png(path, img):
    raw = b"".join(b"\0" + img[y].tobytes() for y in range(img.shape[0]))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.shape[1], img.shape[0], 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    for name, spec in SKINS.items():
        img = person(**spec)
        open(os.path.join(HERE, name + ".b64"), "w").write(base64.b64encode(img.tobytes()).decode())
        png(os.path.join(HERE, name + ".png"), np.kron(img, np.ones((8, 8, 1), np.uint8)))
        print("painted", name, "slim" if spec["slim"] else "wide")
