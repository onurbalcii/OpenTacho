"""app/assets/sounds/*.wav dosyalarını tek bir kaynak sesten üretir (ffmpeg gerekir).

    python tools/make_sounds.py [kaynak.mp3|kaynak.wav]

Kaynak verilmezse app/assets/sounds/notification.mp3 kullanılır.
  warn.wav  – kaynak ses (15 dk kala ikaz)
  alert.wav – kaynak ses iki kez arka arkaya (ihlal)
  done.wav  – kaynak ses (mola / dinlenme tamamlandı)
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "assets", "sounds")
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "notification.mp3")
FF = shutil.which("ffmpeg")
if FF is None:
    sys.exit("ffmpeg bulunamadı (winget install Gyan.FFmpeg)")
if not os.path.exists(SRC):
    sys.exit("kaynak ses yok: " + SRC)

os.makedirs(OUT, exist_ok=True)
fmt = ["-ac", "2", "-ar", "44100", "-sample_fmt", "s16"]
subprocess.check_call([FF, "-v", "error", "-y", "-i", SRC, *fmt, os.path.join(OUT, "warn.wav")])
subprocess.check_call([FF, "-v", "error", "-y", "-i", SRC, *fmt, os.path.join(OUT, "done.wav")])
subprocess.check_call([FF, "-v", "error", "-y", "-i", SRC, "-filter_complex",
                       "[0:a]asplit=2[a][b];[a]apad=pad_dur=0.12[a1];[a1][b]concat=n=2:v=0:a=1[out]",
                       "-map", "[out]", *fmt, os.path.join(OUT, "alert.wav")])
print("yazıldı:", OUT)
