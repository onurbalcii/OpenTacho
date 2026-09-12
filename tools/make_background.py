# -*- coding: utf-8 -*-
"""Van Gogh 'Yıldızlı Gece' esintili, fırça darbeleriyle boyanmış arka plan üretir (bg_starry.jpg)."""
import os, math, colorsys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "assets")   # çıktı: app/assets/
W, H = 1400, 2000
rng = np.random.default_rng(1889)   # tablonun yılı


def value_noise(shape, gy, gx):
    h, w = shape
    gy, gx = int(gy), int(gx)
    grid = rng.random((gy + 1, gx + 1))
    ys = np.linspace(0, gy, h, endpoint=False); xs = np.linspace(0, gx, w, endpoint=False)
    y0 = ys.astype(int); x0 = xs.astype(int)
    fy = ys - y0; fx = xs - x0
    fy = fy * fy * (3 - 2 * fy); fx = fx * fx * (3 - 2 * fx)
    a = grid[y0][:, x0]; b = grid[y0][:, x0 + 1]; c = grid[y0 + 1][:, x0]; d = grid[y0 + 1][:, x0 + 1]
    top = a + (b - a) * fx[None, :]; bot = c + (d - c) * fx[None, :]
    return top + (bot - top) * fy[:, None]


def fbm(shape, base, octaves=4):
    out = np.zeros(shape); amp = 1.0; tot = 0.0
    for o in range(octaves):
        g = base * (2 ** o)
        out += amp * value_noise(shape, int(g * shape[0] / shape[1]) + 1, g)
        tot += amp; amp *= 0.5
    return out / tot


# ---- akış alanı (düşük çözünürlükte hesapla) ----
fs = 4
fh, fw = H // fs, W // fs
yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float64)
xx *= fs; yy *= fs
turb = fbm((fh, fw), 3.0, 4) * 2 * math.pi * 1.6
vx = 0.9 * np.cos(turb) + 0.7          # hafif yatay sürüklenme
vy = 0.9 * np.sin(turb) * 0.6

# büyük girdaplar (tablodaki iki sarmal)
vortices = [(0.36 * W, 0.24 * H, 0.17 * W, 3.2), (0.66 * W, 0.19 * H, 0.13 * W, -2.6), (0.15 * W, 0.42 * H, 0.10 * W, 1.6)]
for cx, cy, sig, strength in vortices:
    dx, dy = xx - cx, yy - cy
    d2 = dx * dx + dy * dy
    wgt = strength * np.exp(-d2 / (2 * sig * sig))
    # teğet yön
    n = np.sqrt(d2) + 1e-6
    vx += wgt * (-dy / n)
    vy += wgt * (dx / n)
norm = np.sqrt(vx * vx + vy * vy) + 1e-6
fxn, fyn = vx / norm, vy / norm

# aydınlık bantlar (tablodaki ışıklı akıntılar)
lum = fbm((fh, fw), 2.2, 3)

# ---- tepe çizgisi ----
xs_full = np.arange(W)
hill = 0.71 * H + 0.035 * H * np.sin(xs_full / W * 3.4 + 0.6) + 0.025 * H * np.sin(xs_full / W * 9.1 + 2.0)

# ---- yıldızlar ve ay ----
stars = [(0.20, 0.10, 34), (0.47, 0.06, 26), (0.80, 0.30, 30), (0.10, 0.28, 24), (0.58, 0.35, 22),
         (0.90, 0.50, 20), (0.33, 0.44, 18), (0.72, 0.52, 16), (0.05, 0.58, 15), (0.50, 0.56, 14)]
stars = [(sx * W, sy * H, r) for sx, sy, r in stars]
moon = (0.86 * W, 0.11 * H, 62)

img = Image.new("RGB", (W, H), (10, 20, 66))
draw = ImageDraw.Draw(img)
# taban gradyanı
grad = Image.linear_gradient("L").resize((W, H))
top_c, mid_c, bot_c = np.array([9, 17, 62]), np.array([16, 44, 108]), np.array([7, 18, 46])
g = np.asarray(grad).astype(np.float64)[..., None] / 255.0
base = np.where(g < 0.55, top_c + (mid_c - top_c) * (g / 0.55), mid_c + (bot_c - mid_c) * ((g - 0.55) / 0.45))
img = Image.fromarray(base.astype(np.uint8))
draw = ImageDraw.Draw(img)


def hsv_jitter(rgb, dh=0.01, ds=0.10, dv=0.10):
    r, g, b = [c / 255.0 for c in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h = (h + rng.uniform(-dh, dh)) % 1.0
    s = min(1, max(0, s + rng.uniform(-ds, ds)))
    v = min(1, max(0, v + rng.uniform(-dv, dv)))
    return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


SKY = [((19, 46, 122), 30), ((30, 74, 160), 30), ((52, 110, 196), 18), ((92, 158, 222), 10), ((150, 205, 240), 5), ((233, 240, 250), 1.5)]
HILL = [((8, 18, 40), 35), ((12, 34, 62), 30), ((17, 52, 70), 18), ((24, 66, 60), 10), ((40, 90, 80), 5)]
YEL = [((242, 190, 70), 40), ((250, 214, 110), 35), ((255, 238, 180), 25)]


def pick(pal):
    cols, wts = zip(*pal); wts = np.array(wts, float); wts /= wts.sum()
    return cols[rng.choice(len(cols), p=wts)]


def field(x, y):
    i = min(fh - 1, max(0, int(y) // fs)); j = min(fw - 1, max(0, int(x) // fs))
    return fxn[i, j], fyn[i, j], lum[i, j]


def stroke(x, y, color, length, width, steps=4, ang=None):
    pts = [(x, y)]
    for _ in range(steps):
        if ang is None:
            dx, dy, _ = field(x, y)
        else:
            dx, dy = math.cos(ang), math.sin(ang)
        x += dx * length / steps; y += dy * length / steps
        pts.append((x, y))
    draw.line(pts, fill=color, width=int(width), joint="curve")


# ---- gökyüzü darbeleri ----
for _ in range(52000):
    x = rng.uniform(0, W); y = rng.uniform(0, hill[int(min(W - 1, x))] + 40)
    _, _, l = field(x, y)
    col = pick(SKY)
    # aydınlık bantlarda daha açık
    if l > 0.62 and rng.random() < 0.5:
        col = pick(SKY[2:])
    # girdap çekirdeklerine yakın: daha parlak
    for cx, cy, sig, _s in vortices:
        if (x - cx) ** 2 + (y - cy) ** 2 < (0.55 * sig) ** 2 and rng.random() < 0.35:
            col = pick(SKY[3:])
    # yıldız haleleri: sarıya kay
    for sx, sy, r in stars:
        d = math.hypot(x - sx, y - sy)
        if d < r * 2.6 and rng.random() < max(0, 1 - d / (r * 2.6)) * 0.9:
            col = pick(YEL)
    stroke(x, y, hsv_jitter(col), rng.uniform(22, 48), rng.uniform(4.5, 9.5))

# ---- yıldız halkaları ve çekirdekleri ----
for sx, sy, r in stars:
    for ring in np.linspace(r * 0.9, r * 2.4, 5):
        for a in np.linspace(0, 2 * math.pi, int(ring / 4), endpoint=False):
            a += rng.uniform(-0.15, 0.15)
            px, py = sx + math.cos(a) * ring, sy + math.sin(a) * ring
            col = pick(YEL) if ring < r * 1.6 else pick(SKY[3:] + YEL[:1])
            stroke(px, py, hsv_jitter(col), rng.uniform(10, 18), rng.uniform(3.5, 6), steps=2, ang=a + math.pi / 2)
    draw.ellipse((sx - r * 0.55, sy - r * 0.55, sx + r * 0.55, sy + r * 0.55), fill=(255, 240, 190))
    draw.ellipse((sx - r * 0.3, sy - r * 0.3, sx + r * 0.3, sy + r * 0.3), fill=(255, 252, 230))

# ---- ay ----
mx, my, mr = moon
for ring in np.linspace(mr * 1.1, mr * 2.6, 6):
    for a in np.linspace(0, 2 * math.pi, int(ring / 3.5), endpoint=False):
        a += rng.uniform(-0.12, 0.12)
        px, py = mx + math.cos(a) * ring, my + math.sin(a) * ring
        col = pick([((250, 190, 60), 40), ((255, 220, 120), 40), ((255, 240, 190), 20)]) if ring < mr * 1.8 else pick(SKY[3:] + YEL[:1])
        stroke(px, py, hsv_jitter(col), rng.uniform(12, 20), rng.uniform(4, 7), steps=2, ang=a + math.pi / 2)
draw.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(252, 206, 90))
draw.ellipse((mx - mr * 1.05 + mr * 0.55, my - mr * 1.05 - mr * 0.25, mx + mr * 1.05 + mr * 0.55, my + mr * 1.05 - mr * 0.25), fill=hsv_jitter((250, 200, 80), 0, 0.05, 0.02))
draw.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(253, 214, 100))
# hilal: sağ üstten koyu diskle kes, sonra kesik alanı gök darbeleriyle dokula
cutx, cuty, cutr = mx + mr * 0.5, my - mr * 0.35, mr
draw.ellipse((cutx - cutr, cuty - cutr, cutx + cutr, cuty + cutr), fill=(24, 60, 140))
for _ in range(700):
    a = rng.uniform(0, 2 * math.pi); d = cutr * math.sqrt(rng.uniform(0, 1)) * 0.97
    px, py = cutx + math.cos(a) * d, cuty + math.sin(a) * d
    stroke(px, py, hsv_jitter(pick(SKY[1:4])), rng.uniform(8, 14), rng.uniform(3, 5), steps=2, ang=a + math.pi / 2)

# ---- tepeler ----
for _ in range(16000):
    x = rng.uniform(0, W); hy = hill[int(min(W - 1, x))]
    y = rng.uniform(hy - 10, H)
    depth = (y - hy) / (H - hy)
    col = pick(HILL[:3]) if depth > 0.5 else pick(HILL)
    slope = (hill[int(min(W - 1, x + 12))] - hill[int(max(0, x - 12))]) / 24.0
    ang = math.atan(slope) + rng.uniform(-0.25, 0.25)
    stroke(x, y, hsv_jitter(col, 0.01, 0.08, 0.08), rng.uniform(20, 46), rng.uniform(5, 10), steps=3, ang=ang)
# tepe sırtı: açık kontur
for x in range(0, W, 6):
    hy = hill[x]
    stroke(x, hy + rng.uniform(-3, 3), hsv_jitter((60, 120, 150), 0.01, 0.1, 0.15), 14, 3, steps=2,
           ang=math.atan((hill[min(W - 1, x + 6)] - hy) / 6.0))
# köy ışıkları
for _ in range(14):
    x = rng.uniform(0.05 * W, 0.95 * W); y = hill[int(x)] + rng.uniform(40, 220)
    draw.rectangle((x, y, x + rng.uniform(5, 9), y + rng.uniform(7, 12)), fill=(255, 225, 120))

img = img.filter(ImageFilter.GaussianBlur(0.7))
# vinyet
vy_, vx_ = np.mgrid[0:H, 0:W].astype(np.float64)
vig = 1 - 0.35 * (((vx_ - W / 2) / (W / 2)) ** 2 + ((vy_ - H / 2) / (H / 2)) ** 2) ** 1.2
arr = np.asarray(img).astype(np.float64) * np.clip(vig, 0, 1)[..., None]
img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
img.save(os.path.join(APP, "bg_starry.jpg"), quality=86, optimize=True)
img.resize((420, 600), Image.LANCZOS).save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bg_preview.png"))
print("done", os.path.getsize(os.path.join(APP, "bg_starry.jpg")) // 1024, "KB")
