# -*- coding: utf-8 -*-
"""Logo filigran deseni: çapraz (döndürülmüş) ve şaşırtmalı tekrar eden şeffaf karo (logo_tile.png)."""
import os
from PIL import Image
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "assets")   # çıktı: app/assets/
logo = Image.open(os.path.join(APP, "logo.png")).convert("RGBA")

SCALE = 2                      # retina keskinliği için 2x üret, CSS'te yarı boyutta göster
TW, TH = 240 * SCALE, 200 * SCALE
LOGO_W = 112 * SCALE
ANGLE = 22                     # çapraz
ALPHA = 0.11                   # filigran şeffaflığı

lw = LOGO_W
lh = int(logo.height * lw / logo.width)
small = logo.resize((lw, lh), Image.LANCZOS)
rot = small.rotate(ANGLE, expand=True, resample=Image.BICUBIC)
# alfa düşür
a = rot.getchannel("A").point(lambda v: int(v * ALPHA))
rot.putalpha(a)

tile = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
def stamp(cx, cy):
    # karo sınırından taşan kısımlar sarılsın diye 9 komşuya da bas
    for dx in (-TW, 0, TW):
        for dy in (-TH, 0, TH):
            tile.alpha_composite(rot, (int(cx - rot.width / 2 + dx), int(cy - rot.height / 2 + dy)))
stamp(TW * 0.25, TH * 0.25)
stamp(TW * 0.75, TH * 0.75)     # tuğla düzeni: yarım kaydırılmış ikinci logo
tile.save(os.path.join(APP, "logo_tile.png"), optimize=True)

# önizleme: koyu zeminde 3x3 tekrar
prev = Image.new("RGBA", (TW * 3 // SCALE, TH * 3 // SCALE), (14, 22, 66, 255))
t = tile.resize((TW // SCALE, TH // SCALE), Image.LANCZOS)
for i in range(3):
    for j in range(3):
        prev.alpha_composite(t, (i * TW // SCALE, j * TH // SCALE))
prev.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tile_preview.png"))
print("done", tile.size, os.path.getsize(os.path.join(APP, "logo_tile.png")) // 1024, "KB")
