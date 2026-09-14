# -*- coding: utf-8 -*-
"""
OpenTacho — Euro Truck Simulator 2 / American Truck Simulator için basit takograf
------------------------------------------------------------------------------
Kural (oyun saatiyle):  4,5 sa sürüş -> 45 dk mola -> 4,5 sa sürüş -> 11 sa dinlenme -> baştan.

Oyun saati ve hız, RenCloud scs-telemetry eklentisinin paylaşımlı belleğinden
(Local\\SCSTelemetry) okunur. Oyun yoksa manuel moda geçip saati elle ilerletebilirsin.

Saat dilimi: oyun HUD'da aracın bulunduğu ülkenin yerel saatini gösterir, telemetri ise
temel saati verir. Fark, oyunun en yeni kayıt dosyasındaki (game.sii) time_zone alanından
okunur (SII_Decrypt.dll ile çözülür) ve saat göstergesine uygulanır.

Atla: oyunun konsoluna "g_set_time SS DD" komutu gönderilir (konsol açık olmalı: g_console 1).
"""
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time
import traceback

import webview

if sys.platform != "win32":
    # Telemetri paylasimli bellegi, konsola tus gonderme, global kisayol ve ses Windows API'leri ile calisiyor.
    sys.exit("OpenTacho only runs on Windows 10/11 (the game plugin exposes telemetry through Windows shared memory). "
             "OpenTacho yalnizca Windows 10/11 uzerinde calisir.")

# Kaynaklar (app/, lang/, lib/) ve kullanıcı verisi (state, history, log) ayrı yerlerde olabilir:
# PyInstaller ile paketlenince kaynaklar _internal/ içinde, veri ise .exe'nin yanında tutulur.
if getattr(sys, "frozen", False):
    APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = APP_DIR
STATE_FILE = os.path.join(DATA_DIR, "state.json")
HIST_FILE = os.path.join(DATA_DIR, "history.json")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")       # profil başına sayaçlar: <oyun>_<profil id>.json (+ .hist.json)
# ETS2/ATS seviye tablosu (def/economy_data.sii level_xp[]: bir sonraki seviye için gereken XP; tablo bitince son değer tekrar eder)
LEVEL_XP = [200, 500, 700, 900, 1000, 1100, 1300, 1600, 1700, 2100, 2300, 2600, 2700, 2900, 3000, 3100, 3400, 3700, 4000, 4300,
            4600, 4700, 4900, 5200, 5700, 5900, 6000, 6200, 6600, 6800]
LEVEL_LAST = 150
PLAN_DEFAULT_KMH = 70.0   # ortalama hız öğrenilene kadar planlayıcının varsayımı (oyun km / oyun saati)
# İhlal ciddiyeti (AB 2016/403 Ek III sınıfları, aşım dk): (hafif üst sınırı, ciddi üst sınırı); üstü çok ciddi
SEVERITY = {"block": (30, 90), "daily": (60, 120), "weekly": (240, 840), "fortnight": (600, 1350), "span": (60, 150), "wrest": (180, 540)}
FINES = {"minor": 150, "serious": 450, "vserious": 1200}   # sanal ceza (€, yaklaşık AB ortalaması; yalnızca oyun içi keyif)


def severity(kind, over):
    a, b = SEVERITY.get(kind, (60, 120))
    return "minor" if over <= a else ("serious" if over <= b else "vserious")
ERR_FILE = os.path.join(DATA_DIR, "error.log")
UI_DIR = os.path.join(APP_DIR, "app")              # pencerelerin yüklediği her şey (html + assets)
UI_FILE = os.path.join(UI_DIR, "main.html")
OVERLAY_FILE = os.path.join(UI_DIR, "mini.html")
ASSET_DIR = os.path.join(UI_DIR, "assets")
SOUND_DIR = os.path.join(ASSET_DIR, "sounds")
ICON_FILE = os.path.join(ASSET_DIR, "logo.ico")
LANG_DIR = os.path.join(APP_DIR, "lang")
SII_DLL = os.path.join(APP_DIR, "lib", "SII_Decrypt.dll")
if not os.path.exists(SII_DLL) and os.path.exists(os.path.join(DATA_DIR, "SII_Decrypt.dll")):
    SII_DLL = os.path.join(DATA_DIR, "SII_Decrypt.dll")
APP_NAME = "OpenTacho"
WINDOW_TITLE = APP_NAME
MINI_W, MINI_H = 540, 76          # mini şerit penceresi (px)
DEFAULT_LANG = "tr"
DEFAULT_THEME = "vangogh"
# Geri bildirim formu, dile göre (bilinmeyen dil -> "en"). Kendi çatalında kendi form bağlantılarını yaz.
FEEDBACK_URLS = {
    "tr": "https://docs.google.com/forms/d/e/1FAIpQLSe4477g_Gw-Ci1Kllu3tq83CZY5569yLxG6tfmzh6jVMtYInQ/viewform",
    "en": "https://forms.gle/BLknwz91JuyHjrqA6",
}
ETS2_DOCS = os.path.join(os.path.expanduser("~"), "Documents", "Euro Truck Simulator 2")
ATS_DOCS = os.path.join(os.path.expanduser("~"), "Documents", "American Truck Simulator")
# Pencere arka planı (ilk boyamadan önce görünen renk) temaya göre
THEME_BG = {"vangogh": "#0a1440", "dark": "#0f1216", "light": "#eef1f6"}

# ---------------- Kurallar (hepsi OYUN dakikası) ----------------
DRIVE_BLOCK = 270      # 4,5 saat: bir molaya kadar sürüş
DRIVE_DAILY = 540      # 9 saat: 11 saatlik dinlenmeye kadar toplam sürüş (2 x 4,5)
BREAK_MIN = 45         # mola
BREAK_PART1 = 15       # bölünmüş mola (15 + 30): 1. parça en az 15 dk...
BREAK_PART2 = 30       # ...2. parça en az 30 dk, aynı 4,5 saatlik blok içinde
ALERT_BEFORE = 15      # sesli ön uyarı: bitime bu kadar dk kala
REST_MIN = 660         # 11 saat günlük dinlenme
SPLIT_PART1 = 180      # bölünmüş günlük dinlenme: 1. kısım en az 3 saat...
SPLIT_PART2 = 540      # ...2. kısım en az 9 saat (AB 561/2006, md. 4g)
# Haftalık kurallar (isteğe bağlı; varsayılan basit mod): AB 561/2006 md. 6–8
DRIVE_WEEK = 3360      # 56 sa haftalık sürüş (takvim haftası: Pzt 00:00 – Paz 24:00, oyun yerel saati)
DRIVE_FORTNIGHT = 5400 # 90 sa iki ardışık haftada
DRIVE_DAILY_EXT = 600  # günlük sürüş haftada 2 kez 10 saate uzatılabilir
EXT_PER_WEEK = 2
REST_REDUCED = 540     # günlük dinlenme haftada 3 kez 9 saate düşürülebilir
RED_PER_WEEK = 3
WREST_MIN = 2700       # 45 sa haftalık dinlenme
WREST_REDUCED = 1440   # 24 sa azaltılmış haftalık dinlenme (telafi izlenmez)
WREST_SPAN = 6 * 1440  # bir önceki haftalık dinlenmenin bitiminden en geç 6×24 sa sonra başlamalı
WEEK_MIN = 7 * 1440
SKIP_CHUNK_MAX = 1380  # g_set_time tek seferde günün saatini kurar: her adım en fazla 23 sa ileri


def xp_to_level(xp):
    """Toplam XP → sürücü seviyesi (economy_data.sii tablosu)."""
    try:
        xp = int(xp)
    except Exception:
        return None
    lvl, need = 0, 0
    while lvl < LEVEL_LAST:
        step = LEVEL_XP[lvl] if lvl < len(LEVEL_XP) else LEVEL_XP[-1]
        if xp < need + step:
            break
        need += step
        lvl += 1
    return lvl


def sii_text(v):
    """SII metin değeri: ters bölü-x-NN kaçışlarını UTF-8 baytı olarak çözer."""
    try:
        b = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), v).encode("latin-1", "replace")
        return b.decode("utf-8", "replace")
    except Exception:
        return v
JUMP_MIN = 30          # tek tikte >= bu kadar dk ilerlerse (uyku/feribot/tren) dinlenme sayılır
MOVE_KMH = 5.0         # bunun üstü "hareket ediyor"
YARD_MAX_KMH = 40.0    # iç hareket bu hızın üstünde otomatik biter (yükleme sahası tek seferlik olduğu için toleranslı)
DRIVE_GRACE = 2        # durduktan sonra bu kadar dk daha sürüş sayılır (trafik ışığı vb.)
AUTO_BREAK_REMAINING = 10   # kalan sürüş bu kadar dk ve altındaysa...
AUTO_BREAK_STOPPED = 5      # ...araç bu kadar oyun dk durunca otomatik mola
ETS2_BASE_TZ = 120     # oyunun temel saat dilimi (CEST, dakika); HUD yerel = temel + (ülke_tz - bu)
SKIP_MARGIN = 1        # atlarken hedefin üstüne eklenen pay (dk)
SAVE_FRESH_MIN = 30    # atlamadan önce kayıt en fazla bu kadar oyun dk eski olabilir (dilim güncel olsun)
DELIVERY_NEAR_M = 100  # rotaya bu kadar metre kalınca (aktif işte) teslimat sahası: otomatik iç hareket
DELIVERY_FAR_M = 300   # tekrar bu kadar uzaklaşınca otomatik iç hareket biter
DELIVERY_MAX_KMH = 20  # teslimat sahası tespiti için hız üst sınırı
JOB_EVENT_WINDOW = 30  # sn: iş olayı (başlangıç/bitiş/yük bayrağı) ile zaman atlaması bu kadar yakınsa "yükleme" sayılır
JUMP_HOLD_S = 30       # sn: zaman atlaması karara bağlanmadan önce iş olayı beklenir (kendi dorseyle yüklemede
                       #     onJob ve isCargoLoaded atlamadan 6–20 sn SONRA geliyor; 4 sn yetmiyordu)
SKIP_WINDOW = 25       # sn: kendi g_set_time komutumuzdan bu kadar süre içindeki atlama beklemeden dinlenme sayılır
HIST_MINUTES = 12 * 60 # kayıt geri yükleme için tutulan sayaç geçmişi (oyun dk)
ROLLBACK_MIN = 2       # oyun saati en az bu kadar dk geri giderse "kayıt yüklendi" sayılır
TICK = 0.25            # saniye

STATUSES = ("DRIVING", "ON_DUTY", "OFF_DUTY", "YARD_MOVE")
THEMES = ("vangogh", "dark", "light", "custom")
DEFAULT_CUSTOM = {"win": "#1c2a5e", "accent": "#fdca4f", "bg": False}   # özel tema: pencere rengi, vurgu rengi, arka plan görseli var mı
CUSTOM_BG_FILE = os.path.join(DATA_DIR, "custom_bg.img")                 # kullanıcının seçtiği görsel (jpeg/png), exe'nin yanında
CUSTOM_BG_MAX = 15 * 1024 * 1024


class I18n:
    """lang/<kod>.json sözlükleri; eksik anahtarlar İngilizceye, o da yoksa anahtarın kendisine düşer."""

    def __init__(self):
        self.code = "en"
        self.d = {}
        self.fallback = {}
        self.load(DEFAULT_LANG)

    @staticmethod
    def _read(code):
        try:
            with open(os.path.join(LANG_DIR, code + ".json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_error("dil dosyası okunamadı (%s): %r" % (code, e))
            return {}

    def load(self, code):
        code = (code or DEFAULT_LANG).lower()
        d = self._read(code)
        if not d and code != "en":
            code, d = "en", self._read("en")
        self.code = code
        self.d = d
        self.fallback = self._read("en") if code != "en" else d

    def __call__(self, key, **kw):
        s = self.d.get(key)
        if s is None:
            s = self.fallback.get(key, key)
        if kw:
            try:
                return s.format(**kw)
            except Exception:
                return s
        return s

    def lst(self, key):
        v = self.d.get(key) or self.fallback.get(key) or []
        return v if isinstance(v, list) else []

    def strings(self):
        out = dict(self.fallback)
        out.update(self.d)
        return out

    @staticmethod
    def available():
        langs = []
        try:
            for fn in sorted(os.listdir(LANG_DIR)):
                if fn.endswith(".json"):
                    code = fn[:-5]
                    name = I18n._read(code).get("_name", code)
                    langs.append({"code": code, "name": name})
        except Exception:
            pass
        return langs or [{"code": "en", "name": "English"}]


L = I18n()


def feedback_url():
    return FEEDBACK_URLS.get(L.code, FEEDBACK_URLS.get("en", ""))


def WEEKDAY(i):
    wd = L.lst("weekdays") or ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return wd[int(i) % 7]

DEFAULT_STATE = {
    "mode": "auto",            # auto | manual
    "status": "ON_DUTY",
    "drive_block": 0,          # son geçerli mola/dinlenmeden beri sürüş (dk)
    "drive_daily": 0,          # son 11 sa dinlenmeden beri sürüş (dk)
    "rest": 0,                 # son sürüşten beri kesintisiz dinlenme (dk)
    "break_credited": False,   # bu günde 45 dk mola yapıldı mı
    "split_rest": True,        # bölünmüş günlük dinlenmeye (3+9) izin ver; kapalıysa hep tek parça 11 sa
    "rest_part1": 0,           # bölünmüş dinlenmenin tamamlanmış 1. kısmı (dk, 0 = yok)
    "rest_daily_done": False,  # bu dinlenme süresi içinde günlük dinlenme tamamlandı mı
    "day_segments": [],        # günün akışı: [{"t": "drive"|"rest", "m": dk, "k": rest türü, "a": başlangıç (yerel dk)}]
    "day_violations": [],      # bugünkü ihlaller: [{"t": yerel dk, "kind": block|daily|weekly|fortnight|span|wrest, "over": aşım dk, "open": bool}]
    "weekly_rules": False,     # haftalık kurallar (56/90 sa, gün yayılımı, haftalık dinlenme); kapalı = basit mod
    "auto_ext": True,          # otomatik uzatılmış sürüş: 9 sa aşılırsa gün 10 saate uzar (haftada 2×)
    "ext_drive": False,        # bugün günlük sürüş 10 saate uzatıldı (9 sa aşıldı, hak bu hafta harcandı)
    "ext_consumed": False,     # (ext_drive ile aynı anda set edilir; eski kayıtlarla uyum için tutulur)
    "auto_red": True,          # otomatik kısa dinlenme: 9 sa dinlenip yola çıkınca hak varsa (haftada 3×) gün tamamlanmış sayılır
    "week": {"start": None, "ext_used": 0, "red_used": 0, "wrest": None},   # içinde bulunulan takvim haftası
    "weeks_meta": {},          # geçmiş haftalar: {"<hafta başı>": {"ext_used", "red_used", "wrest"}}
    "last_wrest_end": None,    # son haftalık dinlenmenin bittiği yerel dk (sonraki en geç +6 gün)
    "wrest_kind": None,        # süren dinlenme haftalık dinlenme eşiğini geçtiyse "reduced" | "regular"
    "days": [],                # arşiv: günlük dinlenme tamamlanınca (ya da sıfırlamada) kapanan günler (takograf geçmişi)
    "last_abs": None,          # en son görülen oyun saati (dk)
    "last_move_abs": None,
    "manual_abs": 8 * 60,      # manuel saat (dk, float)
    "manual_rate": 19.0,       # manuel modda 1 gerçek sn = kaç oyun sn
    "on_top": True,
    "auto_break": True,        # kalan sürüş azken durunca otomatik mola
    "split_break": True,       # bölünmüş mola (15 + 30) izinli
    "break_part1": 0,          # bu blokta alınmış 1. parça mola (dk, 0 = yok)
    "rest_credited": False,    # bu dinlenme süresi içinde mola kredilendi mi
    "sounds": True,            # sesli uyarılar
    "onboarded": False,        # ilk açılış: dil seçimi + rehber turu tamamlandı mı
    "hotkey": {"mods": 2, "vk": 96, "name": "Ctrl + Num 0"},   # mini şerit kısayolu (MOD_CONTROL, VK_NUMPAD0)
    "mini_pos": None,          # mini şerit konumu {"x","y"}
    "mini_opacity": 0.7,       # mini şerit pencere opaklığı (0.3–1.0; LWA_ALPHA, oyun altından görünür)
    "offjob_rest": True,       # görev dışındayken (aktif teslimat yok) dinlenme yine sayılsın mı (varsayılan açık)
    "tz_adjust": 0,            # saat göstergesine elle eklenen düzeltme (dk)
    "set_time_frame": "local", # g_set_time hangi saati alıyor: local (HUD saati, test edildi) | base
    "win": None,               # pencere konumu/boyutu {"x","y","w","h"}
    "prov": None,              # geçici sayım (iş yok ama GPS rotası var): başlangıç anındaki sayaç görüntüsü
    "lang": DEFAULT_LANG,      # arayüz dili (lang/<kod>.json)
    "theme": DEFAULT_THEME,    # vangogh | dark | light | custom
    "custom": dict(DEFAULT_CUSTOM),   # özel tema ayarları (bkz. DEFAULT_CUSTOM)
    "profile_key": None,       # sayaçların ait olduğu oyun profili ("<oyun>:<profil id>"); None = henüz tanınmadı
    "strict_rest": False,      # sıkı mod: mola/dinlenme yalnızca motor kapalı + el freni çekiliyken sayılır
    "fines": False,            # geçmişte sanal ceza tutarları (€) gösterilsin
    "voice": False,            # sesli anons (Windows konuşma sesi; sayfa okur)
    "spd_km": 0.0, "spd_min": 0.0,   # ortalama hız öğrenme: sürüşte gidilen km / dakika (yumuşatılmış toplamlar)
    "log": [],
}


def log_error(msg):
    try:
        with open(ERR_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def hm(minutes):
    """Dakikayı S:DD biçimine çevirir (negatifse başına - koyar)."""
    minutes = int(round(minutes))
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


def hm_signed(minutes):
    return ("+" if minutes >= 0 else "-") + hm(abs(minutes))


# ---------------- Telemetri (scs-telemetry.dll paylaşımlı bellek) ----------------
class Telemetry:
    NAME = "Local\\SCSTelemetry"
    SIZE = 32 * 1024
    FILE_MAP_READ = 0x0004
    REOPEN_EVERY = 3.0   # oyun kapanmış mı anlamak için handle'ı düzenli olarak yenile

    # Offset'ler: scs-telemetry-common.hpp (PLUGIN_REVID 12)
    OFF_SDK_ACTIVE = 0
    OFF_PAUSED = 4
    OFF_REVISION = 40
    OFF_GAME = 52          # 1 = ETS2, 2 = ATS
    OFF_TIME_ABS = 64      # oyun saati, dakika
    OFF_SCALE = 700
    OFF_SPEED = 948        # m/s
    OFF_DELIVERY = 88      # time_abs_delivery (dk)
    OFF_PLANNED_KM = 100
    STR = 64               # string alanı boyu (9. bölge 2300'den başlar)
    OFF_TRUCK_BRAND = 2364
    OFF_TRUCK_NAME = 2492
    OFF_CARGO = 2620
    OFF_CITY_DST = 2748
    OFF_CITY_SRC = 3004
    OFF_ON_JOB = 4300      # 12. bölge: bool onJob
    OFF_ROUTE_DIST = 1060  # truck_f.routeDistance (m); rota yoksa 0
    OFF_ODOMETER = 1056    # truck_f.truckOdometer (km)
    OFF_PARK_BRAKE = 1566  # truck_b.parkBrake (5. bölge: isCargoLoaded@1564, specialJob@1565, sonra truck_b)
    OFF_ENGINE = 1576      # truck_b.engineEnabled
    OFF_ROUTE_TIME = 1064  # truck_f.routeTime (sn, oyun saati); rota yoksa 0
    OFF_JOB_START = 444    # gameplay_ui.jobStartingTime (oyun dk)
    OFF_CARGO_LOADED = 1564  # truck_b.isCargoLoaded (5. bölge): yükleme/boşaltma anı
    OFF_FERRY = 4306       # special_b.ferry: her feribot kullanımında tersine çevrilir (değişim = olay)
    OFF_TRAIN = 4307       # special_b.train: her tren kullanımında tersine çevrilir
    READ_LEN = 4352

    def __init__(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.restype = wt.HANDLE
        k.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [wt.HANDLE]
        self.k = k
        self.h = None
        self.p = None
        self.opened_at = 0.0
        self.last_try = 0.0

    def _open(self):
        h = self.k.OpenFileMappingW(self.FILE_MAP_READ, False, self.NAME)
        if not h:
            return False
        p = self.k.MapViewOfFile(h, self.FILE_MAP_READ, 0, 0, self.SIZE)
        if not p:
            self.k.CloseHandle(h)
            return False
        self.h, self.p, self.opened_at = h, p, time.monotonic()
        return True

    def close(self):
        if self.p:
            self.k.UnmapViewOfFile(self.p)
        if self.h:
            self.k.CloseHandle(self.h)
        self.h = self.p = None

    def read(self):
        now = time.monotonic()
        if self.h and now - self.opened_at > self.REOPEN_EVERY:
            self.close()
        if not self.h:
            if now - self.last_try < 1.0:
                return None
            self.last_try = now
            if not self._open():
                return None
        buf = ctypes.string_at(self.p, self.READ_LEN)
        speed = struct.unpack_from("<f", buf, self.OFF_SPEED)[0]

        def S(off, n=self.STR):
            return buf[off:off + n].split(b"\0", 1)[0].decode("utf-8", "replace").strip()

        return {
            "truck": (S(self.OFF_TRUCK_BRAND) + " " + S(self.OFF_TRUCK_NAME)).strip(),
            "job": {
                "on": bool(buf[self.OFF_ON_JOB]),
                "src": S(self.OFF_CITY_SRC), "dst": S(self.OFF_CITY_DST), "cargo": S(self.OFF_CARGO),
                "delivery": struct.unpack_from("<I", buf, self.OFF_DELIVERY)[0],
                "km": struct.unpack_from("<I", buf, self.OFF_PLANNED_KM)[0],
                "start": struct.unpack_from("<I", buf, self.OFF_JOB_START)[0],
                "loaded": bool(buf[self.OFF_CARGO_LOADED]),
            },
            "route_m": max(0.0, struct.unpack_from("<f", buf, self.OFF_ROUTE_DIST)[0]),
            "route_s": max(0.0, struct.unpack_from("<f", buf, self.OFF_ROUTE_TIME)[0]),
            "odometer_km": struct.unpack_from("<f", buf, self.OFF_ODOMETER)[0],
            "park_brake": bool(buf[self.OFF_PARK_BRAKE]),
            "engine": bool(buf[self.OFF_ENGINE]),
            "ferry": bool(buf[self.OFF_FERRY]),
            "train": bool(buf[self.OFF_TRAIN]),
            "active": bool(buf[self.OFF_SDK_ACTIVE]),
            "paused": bool(buf[self.OFF_PAUSED]),
            "revision": struct.unpack_from("<I", buf, self.OFF_REVISION)[0],
            "game": struct.unpack_from("<I", buf, self.OFF_GAME)[0],
            "time_abs": struct.unpack_from("<I", buf, self.OFF_TIME_ABS)[0],
            "scale": struct.unpack_from("<f", buf, self.OFF_SCALE)[0],
            "speed_kmh": abs(speed) * 3.6,
        }


# ---------------- Kayıt dosyasından saat dilimi ----------------
class SaveWatcher(threading.Thread):
    """En yeni game.sii kaydını izler, time_zone / time_zone_name alanlarını okur."""
    CHECK_EVERY = 5.0

    def __init__(self):
        super().__init__(daemon=True)
        self.tz = None            # dakika (ör. BST = 60)
        self.tz_name = None       # ör. "BST"
        self.save_time = None     # kayıttaki game_time
        self.save_file = None
        self.error = None
        self._mtime = None
        self._dec = None
        try:
            dll = ctypes.WinDLL(SII_DLL)
            dll.DecryptAndDecodeFile.restype = ctypes.c_int32
            dll.DecryptAndDecodeFile.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            self._dec = dll.DecryptAndDecodeFile
        except Exception as e:
            self.error = "SII_Decrypt.dll yüklenemedi"
            log_error("SII_Decrypt.dll: %r" % e)
        self._tmp = os.path.join(tempfile.gettempdir(), "opentacho_save.sii")
        self.zones_enabled = True
        self.profile = None       # {"key","id","name","company","xp","level","game"} — game.log'daki son yüklenen kayıt
        self._prof_key = None
        self._prof_mtime = None
        self.console_ok = None    # g_console + g_developer açık mı (None = config bulunamadı)
        self.game = 1             # 1 = ETS2, 2 = ATS (Tacho günceller); Belgeler klasörünü seçer
        self._cfg_at = 0.0

    def docs_dir(self):
        return ATS_DOCS if self.game == 2 else ETS2_DOCS

    @staticmethod
    def _uset(txt, name):
        m = re.search(r'^uset %s "([^"]*)"' % re.escape(name), txt, re.M)
        return m.group(1) if m else None

    def _read_config(self, save_file):
        """g_time_zones (0 = kapalı) profil bazlıdır (profiles/<id>/config.cfg); g_console / g_developer
        ise oyunun ana config.cfg dosyasındadır. Atla butonu konsol kapalıyken kilitlenir."""
        cands = []
        if save_file:
            prof = os.path.dirname(os.path.dirname(os.path.dirname(save_file)))
            cands.append(os.path.join(prof, "config.cfg"))
        cands.append(os.path.join(self.docs_dir(), "config.cfg"))
        zones = None
        for c in cands:
            try:
                with open(c, "r", encoding="utf-8", errors="replace") as f:
                    v = self._uset(f.read(), "g_time_zones")
                if v is not None:
                    zones = v != "0"
                    break
            except Exception:
                pass
        self.zones_enabled = True if zones is None else zones
        try:
            with open(os.path.join(self.docs_dir(), "config.cfg"), "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            self.console_ok = self._uset(txt, "g_console") == "1" and self._uset(txt, "g_developer") == "1"
        except Exception:
            self.console_ok = None

    PROFILE_RE = re.compile(r"/home/(profiles|steam_profiles)/([0-9A-Fa-f]+)/")

    def _detect_profile(self):
        """Aktif profil: game.log.txt'deki son 'Loading save … /home/profiles/<id>/' satırı; yoksa en yeni profile.sii."""
        kind = pid = None
        try:
            lp = os.path.join(self.docs_dir(), "game.log.txt")
            size = os.path.getsize(lp)
            with open(lp, "rb") as f:
                f.seek(max(0, size - 262144))
                tail = f.read().decode("utf-8", "replace")
            for m in self.PROFILE_RE.finditer(tail):
                kind, pid = m.group(1), m.group(2)
        except Exception:
            pass
        if not pid:
            cands = [p for d in ("profiles", "steam_profiles") for p in glob.glob(os.path.join(self.docs_dir(), d, "*", "profile.sii"))]
            if cands:
                best = max(cands, key=os.path.getmtime)
                kind = os.path.basename(os.path.dirname(os.path.dirname(best)))
                pid = os.path.basename(os.path.dirname(best))
        if not pid:
            self.profile = None
            self._prof_key = None
            return
        key = "%d:%s" % (self.game, pid)
        psii = os.path.join(self.docs_dir(), kind, pid, "profile.sii")
        try:
            mt = os.path.getmtime(psii)
        except Exception:
            mt = None
        if key == self._prof_key and mt == self._prof_mtime:
            return
        try:
            name = bytes.fromhex(pid).decode("utf-8", "replace").strip() or pid
        except Exception:
            name = pid
        company, xp = "", None
        if self._dec and mt is not None:
            try:
                rc = self._dec(psii.encode("utf-8"), self._tmp.encode("utf-8"))
                if rc == 0:
                    with open(self._tmp, "r", encoding="utf-8", errors="replace") as f:
                        txt = f.read()
                    m = re.search(r'^\s*company_name\s*:\s*"(.*)"\s*$', txt, re.M)
                    if m:
                        company = sii_text(m.group(1))
                    m = re.search(r"^\s*cached_experience\s*:\s*(\d+)\s*$", txt, re.M)
                    if m:
                        xp = int(m.group(1))
                    m = re.search(r'^\s*profile_name\s*:\s*"(.*)"\s*$', txt, re.M)
                    if m and sii_text(m.group(1)).strip():
                        name = sii_text(m.group(1)).strip()
            except Exception as e:
                log_error("profile.sii okunamadı: %r" % e)
        self.profile = {"key": key, "id": pid, "name": name, "company": company, "xp": xp, "level": xp_to_level(xp) if xp is not None else None, "game": self.game}
        self._prof_key, self._prof_mtime = key, mt

    def _newest_save(self):
        pats = [os.path.join(self.docs_dir(), d, "*", "save", "*", "game.sii") for d in ("profiles", "steam_profiles")]
        files = [f for p in pats for f in glob.glob(p)]
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _parse(self, path):
        rc = self._dec(path.encode("utf-8"), self._tmp.encode("utf-8"))
        if rc != 0:
            raise RuntimeError(f"SII_Decrypt rc={rc}")
        with open(self._tmp, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
        m = re.search(r"^\s*time_zone\s*:\s*(-?\d+)\s*$", txt, re.M)
        n = re.search(r'^[ \t]*time_zone_name[ \t]*:[ \t]*"?([^"\r\n]*?)"?[ \t]*$', txt, re.M)
        g = re.search(r"^\s*game_time\s*:\s*(\d+)\s*$", txt, re.M)
        if not m:
            raise RuntimeError("time_zone alanı yok")
        name = n.group(1) if n else ""
        name = re.sub(r"^@@tz_|@@$", "", name).upper() or None
        return int(m.group(1)), name, int(g.group(1)) if g else None

    def run(self):
        while True:
            try:
                self._detect_profile()
                f = self._newest_save()
                if time.monotonic() - self._cfg_at > 60 or f != self.save_file:
                    self._read_config(f)
                    self._cfg_at = time.monotonic()
                if not self._dec:
                    self.save_file = f
                elif f:
                    mt = os.path.getmtime(f)
                    # yazımı bitmiş olsun diye 2 sn bekle
                    if (f, mt) != (self.save_file, self._mtime) and time.time() - mt > 2:
                        self.tz, self.tz_name, self.save_time = self._parse(f)
                        self.save_file, self._mtime = f, mt
                        self.error = None
            except Exception as e:
                self.error = str(e)
                log_error("Kayıt okunamadı: %r" % e)
                self.save_file, self._mtime = f, mt  # aynı dosyayı tekrar tekrar deneme
            time.sleep(self.CHECK_EVERY)


# ---------------- Oyun konsoluna komut gönderme (SendInput) ----------------
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class GameConsole:
    TITLES = ("Euro Truck Simulator 2", "American Truck Simulator")
    SC_TILDE = 0x29
    KEYUP, UNICODE, SCANCODE = 0x0002, 0x0004, 0x0008

    def __init__(self):
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        u.VkKeyScanExW.restype = ctypes.c_short
        u.VkKeyScanExW.argtypes = [wt.WCHAR, wt.HKL]
        u.GetKeyboardLayout.restype = wt.HKL
        u.MapVirtualKeyW.restype = wt.UINT
        u.GetForegroundWindow.restype = wt.HWND
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsIconic.argtypes = [wt.HWND]
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.GetWindowTextLengthW.argtypes = [wt.HWND]
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self.u = u

    def find_window(self):
        found = []

        @WNDENUMPROC
        def cb(hwnd, _):
            if self.u.IsWindowVisible(hwnd):
                n = self.u.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    self.u.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value in self.TITLES:
                        found.append(hwnd)
                        return False
            return True

        self.u.EnumWindows(cb, 0)
        return found[0] if found else None

    def focus(self, hwnd):
        if self.u.IsIconic(hwnd):
            self.u.ShowWindow(hwnd, 9)  # SW_RESTORE
        for attempt in range(2):
            if self.u.GetForegroundWindow() == hwnd:
                return True
            if attempt == 1:  # Alt tuşu hilesi: SetForegroundWindow kısıtını gevşetir
                self._vk(0x12, True)
                self._vk(0x12, False)
            self.u.SetForegroundWindow(hwnd)
            time.sleep(0.2)
        return self.u.GetForegroundWindow() == hwnd

    def _send(self, vk, scan, flags):
        inp = INPUT()
        inp.type = 1
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        self.u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _vk(self, vk, down):
        self._send(vk, self.u.MapVirtualKeyW(vk, 0), 0 if down else self.KEYUP)

    def _scan(self, scan, down):
        self._send(0, scan, self.SCANCODE | (0 if down else self.KEYUP))

    def tap_scan(self, scan):
        self._scan(scan, True)
        time.sleep(0.04)
        self._scan(scan, False)
        time.sleep(0.04)

    def type_text(self, text, hkl):
        for ch in text:
            r = self.u.VkKeyScanExW(ch, hkl)
            if r == -1:
                self._send(0, ord(ch), self.UNICODE)
                self._send(0, ord(ch), self.UNICODE | self.KEYUP)
            else:
                vk, mods = r & 0xFF, (r >> 8) & 0xFF
                held = []
                if mods & 1:
                    held.append(0x10)       # Shift
                if mods & 2:
                    held.append(0x11)       # Ctrl
                if mods & 4:
                    held.append(0x12)       # Alt (AltGr = Ctrl+Alt)
                for m in held:
                    self._vk(m, True)
                self._vk(vk, True)
                time.sleep(0.02)
                self._vk(vk, False)
                for m in reversed(held):
                    self._vk(m, False)
            time.sleep(0.02)

    def send_command(self, command):
        """Oyun penceresini öne alır, konsolu açar, komutu yazar, Enter, konsolu kapatır."""
        hwnd = self.find_window()
        if not hwnd:
            return False, L("console.window_missing")
        if not self.focus(hwnd):
            return False, L("console.focus_failed")
        tid = self.u.GetWindowThreadProcessId(hwnd, None)
        hkl = self.u.GetKeyboardLayout(tid)
        time.sleep(0.25)
        self.tap_scan(self.SC_TILDE)          # konsolu aç
        time.sleep(0.25)
        self.type_text(command, hkl)
        time.sleep(0.1)
        self._vk(0x0D, True)                  # Enter
        time.sleep(0.03)
        self._vk(0x0D, False)
        time.sleep(0.3)
        self.tap_scan(self.SC_TILDE)          # konsolu kapat
        return True, ""


# ---------------- Global kısayol (RegisterHotKey) ----------------
class HotkeyListener(threading.Thread):
    """Ayrı iplikte RegisterHotKey + mesaj döngüsü; kısayol basılınca on_fire çağrılır."""
    WM_HOTKEY = 0x0312
    MOD_NOREPEAT = 0x4000

    def __init__(self, on_fire):
        super().__init__(daemon=True)
        self.on_fire = on_fire
        self.pending = None
        self.registered = False
        self.u = ctypes.WinDLL("user32", use_last_error=True)

    def set(self, mods, vk):
        self.pending = (int(mods), int(vk))

    def run(self):
        u = self.u
        msg = wt.MSG()
        u.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        u.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
        u.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
        while True:
            try:
                if self.pending:
                    mods, vk = self.pending
                    self.pending = None
                    if self.registered:
                        u.UnregisterHotKey(None, 1)
                        self.registered = False
                    if vk:
                        self.registered = bool(u.RegisterHotKey(None, 1, mods | self.MOD_NOREPEAT, vk))
                        if not self.registered:
                            log_error("kısayol kaydedilemedi (mods=%d vk=%d, hata %d)" % (mods, vk, ctypes.get_last_error()))
                while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == self.WM_HOTKEY:
                        try:
                            self.on_fire()
                        except Exception:
                            log_error(traceback.format_exc())
            except Exception:
                log_error(traceback.format_exc())
            time.sleep(0.05)


# ---------------- Pencere: saydamlık ----------------
def _hwnd_of(win):
    try:
        from webview.platforms.winforms import BrowserView
        inst = BrowserView.instances.get(win.uid)
        if inst is not None:
            return int(inst.Handle.ToInt64())
    except Exception:
        pass
    return None


def set_window_alpha(win, opacity, rounded=True):
    """Pencereyi katmanlı yapar ve bütününe alfa uygular (LWA_ALPHA): şeridin arkasındaki oyun görünür.
    WebView2'nin saydam arka planı WinForms altında çalışmadığı için (köşeler beyaz kalıyor) bu yol kullanılır.
    Windows 11'de köşeleri DWM yuvarlar; Windows 10'da köşeler düz kalır."""
    hwnd = _hwnd_of(win)
    if not hwnd:
        return
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
        u.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
        GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x00080000, 0x2
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not ex & WS_EX_LAYERED:
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        a = int(round(max(0.3, min(1.0, float(opacity))) * 255))
        u.SetLayeredWindowAttributes(hwnd, 0, a, LWA_ALPHA)
        if rounded:
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
            pref = ctypes.c_int(2)   # DWMWCP_ROUND (33 = DWMWA_WINDOW_CORNER_PREFERENCE; Win10 yok sayar)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception as e:
        log_error("pencere alfası ayarlanamadı: %r" % e)


def set_toolwindow(win):
    """WS_EX_TOOLWINDOW: görev çubuğunda ve Alt-Tab'da görünmez (ilk gösterimden önce çağrılmalı)."""
    hwnd = _hwnd_of(win)
    if not hwnd:
        return
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
        GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW)
    except Exception as e:
        log_error("araç penceresi stili ayarlanamadı: %r" % e)


# ---------------- Ana pencere: çerçevesiz ama yerel davranış ----------------
THEME_BORDER = {"vangogh": 0x8A4F3A, "dark": 0x3B302A, "light": 0xE2D6CF}   # DWM kenar rengi (COLORREF: 0x00BBGGRR)


def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u.IsZoomed.argtypes = [wt.HWND]
    return u


def _hex_ok(v):
    return isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) is not None


def _hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _mix_hex(h, to, t):
    """h rengini to'ya doğru t oranında karıştırır (#rrggbb)."""
    a, b = _hex_rgb(h), _hex_rgb(to)
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _is_light(h):
    r, g, b = _hex_rgb(h)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.55


def theme_bg_color(s):
    """Pencerenin sayfa boyanmadan önceki arka plan rengi (özel temada pencere renginden türetilir)."""
    theme = s.get("theme") or DEFAULT_THEME
    if theme == "custom":
        w = s["custom"]["win"]
        return _mix_hex(w, "#ffffff", 0.35) if _is_light(w) else _mix_hex(w, "#000000", 0.45)
    return THEME_BG.get(theme, "#0d1117")


def theme_border(s):
    """DWM kenar rengi (COLORREF 0x00BBGGRR): temaya göre; özel temada vurgu rengi."""
    theme = s.get("theme") or DEFAULT_THEME
    if theme == "custom":
        r, g, b = _hex_rgb(s["custom"]["accent"])
        return (b << 16) | (g << 8) | r
    return THEME_BORDER.get(theme, THEME_BORDER["vangogh"])


def theme_hash(s):
    """Sayfa URL'sinin # kısmı: tema (+ özel temada renkler), ilk boyamadan önce okunur."""
    theme = s.get("theme") or DEFAULT_THEME
    h = "theme=" + theme
    if theme == "custom":
        h += "&win=" + s["custom"]["win"][1:] + "&accent=" + s["custom"]["accent"][1:]
    return h


def style_frameless_main(win, border):
    """Windows başlık çubuğu ve çerçevesi yok; Windows 11 köşeleri DWM yuvarlar, ince kenar rengi temaya uyar.
    Taşıma/boyutlandırma/büyütme sayfadaki bar ve kenar tutamaçlarıyla yapılır (WS_THICKFRAME mavi bir üst
    şerit çizdiği ve WinForms'un geri alma hesabını bozduğu için kullanılmaz)."""
    hwnd = _hwnd_of(win)
    if not hwnd:
        return
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        pref = ctypes.c_int(2)                                   # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        col = ctypes.c_uint32(border)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), 4)   # DWMWA_BORDER_COLOR
    except Exception as e:
        log_error("çerçevesiz pencere stili ayarlanamadı: %r" % e)


def work_area(hwnd):
    """Pencerenin bulunduğu ekranın çalışma alanı (görev çubuğu hariç)."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
    u = _user32()
    u.MonitorFromWindow.restype = wt.HMONITOR
    u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
    u.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
    mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
    u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
    return mi.rcWork


def window_syscommand(win, cmd):
    """WM_SYSCOMMAND: 0xF010 taşı, 0xF020 küçült, 0xF030 büyüt, 0xF120 geri al, 0xF000+yön boyutlandır."""
    hwnd = _hwnd_of(win)
    if hwnd:
        u = _user32()
        u.ReleaseCapture()
        u.PostMessageW(hwnd, 0x0112, cmd, 0)


# ---------------- Pencere: her zaman üstte ----------------
def set_topmost(win, flag):
    """pywebview'in on_top ayarı WinForms özelliğini çağıran iş parçacığında değiştirir; GIL tutulurken yapılan
    eşzamanlı SendMessage arayüzü kilitler. SetWindowPos'u ctypes ile (GIL bırakılır) doğrudan çağırıyoruz."""
    hwnd = None
    try:
        from webview.platforms.winforms import BrowserView
        inst = BrowserView.instances.get(win.uid)
        if inst is not None:
            hwnd = int(inst.Handle.ToInt64())
    except Exception:
        pass
    if not hwnd:
        win.on_top = flag
        return
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    u.SetWindowPos(hwnd, HWND_TOPMOST if flag else HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    try:
        win._Window__on_top = bool(flag)   # pywebview'in kendi kaydı da güncel kalsın
    except Exception:
        pass


# ---------------- Takometre mantığı ----------------
class Tacho:
    def __init__(self):
        self.lock = threading.RLock()
        self.s = self._load()
        self.tel = Telemetry()
        self.saves = SaveWatcher()
        self.console = GameConsole()
        self.tel_ok = False        # bellek okunabiliyor ve eklenti aktif
        self.connected = False     # oyun saati kullanılabilir (profil yüklü)
        self.paused = False
        self.speed = 0.0
        self.game = 0
        self.tel_abs = None
        self.tel_rev = 0
        self.tel_scale = 0.0
        self.truck = ""
        self.job = None
        self.offjob = False        # aktif teslimat yok ve GPS rotası yok: sayaçlar duruyor
        self.route_m = 0.0
        self.prev_job_on = None
        self.prev_job_start = None
        self.prev_loaded = None
        self.prev_ferry = None     # feribot/tren bayrakları (tersine çevrilen bool)
        self.prev_train = None
        self.travel_event_at = None   # son feribot/tren olayı (monotonic): atlaması dinlenme
        self.skip_at = None           # son Atla komutu (monotonic): atlaması beklemeden dinlenme
        self.job_event_at = None   # son iş olayı (başlangıç/bitiş) zamanı (monotonic)
        self.pending_jump = None   # karar bekleyen zaman atlaması
        self.auto_yard = None      # otomatik iç hareket nedeni: pickup | delivery | None
        self.alert_prev = {"rem": None, "rest_left": None}
        self.mini_win = None
        self.mini_on = False
        self.mini_lock = threading.RLock()   # aç/kapat art arda gelirse
        self.win_maxed = False               # ana pencere bizim "büyüt"ümüzle çalışma alanını dolduruyor mu
        self.win_normal = None               # büyütme öncesi (x, y, w, h)
        self.exiting = False
        self.api = None
        self.hotkeys = None
        self.hist = []             # [(oyun_dk, görüntü)] — kayıt geri yüklenince geri dönmek için
        self.hist_dirty = False
        self.hist_saved_at = time.monotonic()
        self._hist_load()
        self.no_auto_break = False # kullanıcı molayı elle kapattıysa araç hareket edene kadar otomatik açma
        self.skip = None           # süren atlama işlemi
        self.dirty = False
        self.last_save = time.monotonic()
        self.stop = False
        self.window = None
        self.odo_prev = None       # ortalama hız için son odometre (km)
        self.park_brake = False    # telemetri: el freni
        self.engine = False        # telemetri: motor çalışıyor
        self.voice_seq = 0         # sesli anons: her yeni cümlede artar (sayfa değişince okur)
        self.voice_text = ""
        self.bg_rev = 1            # özel arka plan görseli değişince artar (sayfa yeniden çeker)

    # ---- kalıcılık ----
    def _load(self):
        st = json.loads(json.dumps(DEFAULT_STATE))   # listeler paylaşılmasın
        st["log"] = []
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in DEFAULT_STATE:
                if k in data:
                    st[k] = data[k]
        except FileNotFoundError:
            pass
        except Exception as e:
            log_error("state.json okunamadı: %r" % e)
        if st["status"] not in STATUSES:
            st["status"] = "ON_DUTY"
        if st["mode"] not in ("auto", "manual"):
            st["mode"] = "auto"
        if st["set_time_frame"] not in ("base", "local"):
            st["set_time_frame"] = "local"
        if st.get("theme") not in THEMES:
            st["theme"] = DEFAULT_THEME
        c = st.get("custom") if isinstance(st.get("custom"), dict) else {}
        st["custom"] = {"win": c.get("win") if _hex_ok(c.get("win")) else DEFAULT_CUSTOM["win"],
                        "accent": c.get("accent") if _hex_ok(c.get("accent")) else DEFAULT_CUSTOM["accent"],
                        "bg": bool(c.get("bg")) and os.path.exists(CUSTOM_BG_FILE)}
        L.load(st.get("lang") or DEFAULT_LANG)
        st["lang"] = L.code
        # bağlantı yeniden kurulunca saat yeniden eşitlenir
        st["last_abs"] = None
        st["last_move_abs"] = None
        return st

    def save(self):
        with self.lock:
            data = json.dumps(self.s, ensure_ascii=False, indent=1)
            self.dirty = False
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, STATE_FILE)
        except Exception as e:
            log_error("state.json yazılamadı: %r" % e)

    def maybe_save(self):
        if self.dirty and time.monotonic() - self.last_save > 2.0:
            self.last_save = time.monotonic()
            self.save()
        if self.hist_dirty and time.monotonic() - self.hist_saved_at > 30.0:
            self.hist_saved_at = time.monotonic()
            self._hist_save()

    # ---- yardımcılar ----
    def current_abs(self):
        """Temel (telemetri) saat, dakika."""
        s = self.s
        if s["mode"] == "manual":
            return int(s["manual_abs"])
        if self.connected and self.tel_abs is not None:
            return self.tel_abs
        return s["last_abs"]

    def tz_offset(self):
        """HUD yerel saati - temel saat (dk). Manuel modda 0."""
        if self.s["mode"] == "manual":
            return 0
        off = self.s["tz_adjust"]
        if self.saves.tz is not None and self.saves.zones_enabled and self.game != 2:
            off += self.saves.tz - ETS2_BASE_TZ
        return off

    def local_abs(self):
        a = self.current_abs()
        return None if a is None else a + self.tz_offset()

    def clock_text(self, abs_min):
        if abs_min is None:
            return "--:--"
        return f"{(abs_min % 1440) // 60:02d}:{abs_min % 60:02d}"

    def log(self, msg):
        t = self.clock_text(self.local_abs())
        if self.s["log"] and self.s["log"][-1] == {"t": t, "m": msg}:
            return  # aynı dakikada aynı mesaj (ör. üst üste yeniden başlatma) tekrar yazılmasın
        self.s["log"].append({"t": t, "m": msg})
        self.s["log"] = self.s["log"][-40:]
        self.dirty = True

    def set_status(self, status, note=None):
        if status not in STATUSES or status == self.s["status"]:
            return
        self.s["status"] = status
        self.log(note or L("log.status_started", name=L("status." + status)))

    def remaining(self):
        s = self.s
        r = min(DRIVE_BLOCK - s["drive_block"], self.daily_limit() - s["drive_daily"])
        wi = self.week_info()
        if wi:
            r = min(r, DRIVE_WEEK - wi["drive"], DRIVE_FORTNIGHT - wi["fortnight"])
        return r

    # ---- haftalık kurallar ----
    def weekly_on(self):
        return bool(self.s["weekly_rules"])

    def daily_limit(self):
        """Bugünkü günlük sürüş sınırı: 9 sa, uzatıldıysa 10 sa."""
        return DRIVE_DAILY_EXT if (self.weekly_on() and self.s["ext_drive"]) else DRIVE_DAILY

    def ext_available(self):
        """Bugün otomatik uzatma devreye girebilir mi (hak var, henüz kullanılmadı)."""
        s = self.s
        return self.weekly_on() and bool(s["auto_ext"]) and not s["ext_drive"] and s["week"].get("ext_used", 0) < EXT_PER_WEEK

    def red_available(self):
        """9 saatlik dinlenme kısa günlük dinlenme olarak sayılabilir mi (otomatik açık, hak var)."""
        s = self.s
        return self.weekly_on() and bool(s["auto_red"]) and s["week"].get("red_used", 0) < RED_PER_WEEK

    @staticmethod
    def week_start_of(local):
        """Takvim haftasının başı (Pzt 00:00), yerel dk. Oyun gün 0 = Pazartesi."""
        return local - (local % WEEK_MIN)

    def _today_start(self):
        for x in self.s["day_segments"]:
            if x.get("a") is not None:
                return x["a"]
        return None

    def week_drive(self, ws):
        """ws ile başlayan takvim haftasındaki sürüş (arşiv + bugün); gün, başladığı haftaya yazılır."""
        s = self.s
        total = 0
        for d in s.get("days") or []:
            a = d.get("start")
            if a is not None and ws <= a < ws + WEEK_MIN:
                total += d.get("drive", 0)
        ts = self._today_start()
        if ts is not None and ws <= ts < ws + WEEK_MIN:
            total += sum(x["m"] for x in s["day_segments"] if x["t"] == "drive")
        return total

    def week_info(self):
        """Haftalık kurallar açıksa: hafta başı, bu hafta ve iki haftalık sürüş."""
        if not self.weekly_on():
            return None
        local = self.local_abs()
        if local is None:
            return None
        ws = self.week_start_of(local)
        wd = self.week_drive(ws)
        return {"start": ws, "drive": wd, "fortnight": wd + self.week_drive(ws - WEEK_MIN)}

    def _roll_week(self):
        """Takvim haftası değiştiyse haftalık sayaçları sıfırlar (eskisini weeks_meta'ya yazar)."""
        s = self.s
        local = self.local_abs()
        if local is None:
            return
        ws = self.week_start_of(local)
        w = s["week"]
        if w.get("start") != ws:
            if w.get("start") is not None:
                meta = s.setdefault("weeks_meta", {})
                meta[str(w["start"])] = {"ext_used": w.get("ext_used", 0), "red_used": w.get("red_used", 0), "wrest": w.get("wrest")}
                for k in sorted(meta, key=int)[:-20]:
                    del meta[k]
            s["week"] = {"start": ws, "ext_used": 0, "red_used": 0, "wrest": None}
            self.dirty = True

    def rest_deadline(self):
        """Günlük dinlenmenin en geç başlaması gereken yerel dk (gün başı + 24 sa − dinlenme); gün yayılımı 13/15 sa."""
        if not self.weekly_on() or self.s["rest_daily_done"]:
            return None
        ts = self._today_start()
        return None if ts is None else ts + 1440 - self.daily_need()

    def wrest_due(self):
        """Haftalık dinlenmenin en geç başlaması gereken yerel dk."""
        if not self.weekly_on() or self.s["last_wrest_end"] is None:
            return None
        return self.s["last_wrest_end"] + WREST_SPAN

    def split_active(self):
        """Bölünmüş dinlenme açık ve geçerli bir 1. kısım saklı mı."""
        return bool(self.s["split_rest"]) and self.s["rest_part1"] >= SPLIT_PART1

    def daily_need(self):
        """Günlük dinlenmeyi tamamlamak için gereken kesintisiz süre: 11 sa; 1. kısım varsa ya da kısa dinlenme seçildiyse 9 sa."""
        return SPLIT_PART2 if self.split_active() else REST_MIN

    def break_need(self):
        """Bu blok için gereken mola: 1. parça (>= 15 dk) alındıysa 30, yoksa 45."""
        return BREAK_PART2 if (self.s["split_break"] and self.s["break_part1"] >= BREAK_PART1) else BREAK_MIN

    def rest_target(self):
        """Şu an işe yarayan dinlenme hedefi (mola ihtiyacı ya da günlük ihtiyaç)."""
        s = self.s
        rem_block = DRIVE_BLOCK - s["drive_block"]
        rem_daily = self.daily_limit() - s["drive_daily"]
        next_break = rem_daily > rem_block
        bn = self.break_need()
        if not s["rest_credited"] and s["rest"] < bn and rem_daily > 0 and next_break:
            return bn
        return self.daily_need()

    # ---- sayaçlar ----
    def _seg_add(self, t, d):
        """Gün akışına d dakika ekler; son segment aynı türdeyse uzatır."""
        segs = self.s["day_segments"]
        if segs and segs[-1]["t"] == t and not segs[-1].get("closed"):
            segs[-1]["m"] += d
        else:
            a = self.local_abs()
            segs.append({"t": t, "m": d, "a": (a - d) if a is not None else None})

    def add_rest(self, d):
        s = self.s
        need = self.daily_need()
        before = s["rest"]
        s["rest"] = before + d
        after = s["rest"]
        if not s["rest_daily_done"]:
            self._seg_add("rest", d)
        bn = self.break_need()
        if not s["rest_credited"] and after >= bn:
            s["break_credited"] = True
            s["rest_credited"] = True
            s["drive_block"] = 0
            if bn == BREAK_PART2:
                self.log(L("log.break_split_done", p1=hm(s["break_part1"]), p2=hm(after)))
            else:
                self.log(L("log.break_done"))
            s["break_part1"] = 0
            self._close_violation(("block",))
            self._sound("done")
            self._voice("break_done")
        if not s["rest_daily_done"] and after >= need:
            self._complete_daily(reduced=False)
        if self.weekly_on():
            kind = "regular" if after >= WREST_MIN else ("reduced" if after >= WREST_REDUCED else None)
            if kind:
                if kind != s["wrest_kind"]:
                    s["wrest_kind"] = kind
                    s["week"]["wrest"] = kind
                    self.log(L("log.wrest_" + kind))
                    self._close_violation(("weekly", "fortnight", "wrest"))
                    self._sound("done")
                    self._voice("wrest_done")
                s["last_wrest_end"] = self.local_abs()   # dinlenme sürdükçe bitişi ileri taşı
        self.dirty = True

    def strict_blocked(self):
        """Sıkı mod açıkken dinlenmenin sayılmasını engelleyen durum var mı (motor açık / el freni çekili değil)."""
        s = self.s
        if not s["strict_rest"] or s["mode"] != "auto" or not self.connected:
            return False
        return self.engine or not self.park_brake

    def _voice(self, key, **kw):
        if self.s.get("voice"):
            self.voice_seq += 1
            self.voice_text = L("voice." + key, **kw)

    def _complete_daily(self, reduced):
        """Günlük dinlenme tamamlandı: günü arşivle, sayaçları sıfırla. reduced=True: 9 sa kısa dinlenme (haftalık hak harcanır)."""
        s = self.s
        need = self.daily_need()
        self._archive_day("daily")
        s["drive_block"] = 0
        s["drive_daily"] = 0
        s["break_credited"] = False
        s["rest_daily_done"] = True
        if self.split_active():
            self.log(L("log.split_done", p1=hm(s["rest_part1"]), p2=hm(need)))
        elif reduced:
            s["week"]["red_used"] = s["week"].get("red_used", 0) + 1
            self.log(L("log.red_done", n=s["week"]["red_used"], max=RED_PER_WEEK))
        else:
            self.log(L("log.daily_done"))
        s["rest_part1"] = 0
        s["break_part1"] = 0
        s["day_segments"] = []   # yeni gün: akış sıfırdan
        s["ext_drive"] = s["ext_consumed"] = False   # uzatma yeni günde sıfır
        self._sound("done")
        self._voice("daily_done")

    # ---- ihlal kaydı ve takograf geçmişi ----
    def _track_violation(self):
        """Sürerken aşılan her sınır için ihlal açar / aşımı günceller (blok, günlük, haftalık, iki haftalık, gün yayılımı, haftalık dinlenme)."""
        s = self.s
        rems = {"block": DRIVE_BLOCK - s["drive_block"], "daily": self.daily_limit() - s["drive_daily"]}
        wi = self.week_info()
        if wi:
            rems["weekly"] = DRIVE_WEEK - wi["drive"]
            rems["fortnight"] = DRIVE_FORTNIGHT - wi["fortnight"]
            local = self.local_abs()
            rd, wd = self.rest_deadline(), self.wrest_due()
            if rd is not None and local is not None:
                rems["span"] = rd - local
            if wd is not None and local is not None:
                rems["wrest"] = wd - local
        for kind, rem in rems.items():
            if rem < 0:
                self._open_violation(kind, -rem)

    def _open_violation(self, kind, over):
        v = self.s["day_violations"]
        for x in v:
            if x.get("open") and x["kind"] == kind:
                x["over"] = max(x["over"], over)
                return
        v.append({"t": self.local_abs(), "kind": kind, "over": over, "open": True})

    def _close_violation(self, kinds=None):
        for x in self.s["day_violations"]:
            if x.get("open") and (kinds is None or x["kind"] in kinds):
                x["open"] = False

    def day_summary(self, segs=None, viols=None):
        """Bir günün özeti: toplam sürüş / dinlenme, dinlenme sayısı, aralık, ihlaller."""
        segs = self.s["day_segments"] if segs is None else segs
        viols = self.s["day_violations"] if viols is None else viols
        drive = sum(x["m"] for x in segs if x["t"] == "drive")
        rest = sum(x["m"] for x in segs if x["t"] == "rest")
        starts = [x["a"] for x in segs if x.get("a") is not None]
        start = min(starts) if starts else None
        end = None
        if segs:
            last = segs[-1]
            end = (last["a"] + last["m"]) if last.get("a") is not None else None
        return {"drive": drive, "rest": rest, "rests": sum(1 for x in segs if x["t"] == "rest"),
                "start": start, "end": end, "segments": segs,
                "violations": [{"t": x["t"], "kind": x["kind"], "over": x["over"]} for x in viols]}

    def _archive_day(self, reason):
        """Biten günü geçmişe yazar (sürüş yoksa yazmaz)."""
        s = self.s
        self._close_violation()
        summ = self.day_summary()
        if summ["drive"] <= 0 and not summ["violations"]:
            s["day_violations"] = []
            return
        rec = dict(summ)
        rec["reason"] = reason
        rec["saved"] = time.strftime("%Y-%m-%d %H:%M")
        rec["segments"] = [{"t": x["t"], "m": x["m"], "k": x.get("k"), "a": x.get("a")} for x in summ["segments"]]
        if reason == "daily" and rec["segments"] and rec["segments"][-1]["t"] == "rest" and not rec["segments"][-1]["k"]:
            rec["segments"][-1]["k"] = "part2" if self.split_active() else "daily"
        s["days"] = (s.get("days") or []) + [rec]
        s["days"] = s["days"][-90:]
        s["day_violations"] = []
        self.dirty = True

    def _annotate(self, rec):
        """Gün kaydındaki ihlallere ciddiyet (ve açıksa sanal ceza) ekler; toplam cezayı döndürür."""
        total = 0
        for v in rec.get("violations") or []:
            sev = severity(v.get("kind"), v.get("over", 0))
            v["sev"] = sev
            if self.s.get("fines"):
                v["fine"] = FINES[sev]
                total += FINES[sev]
        rec["fines"] = total if self.s.get("fines") else None
        return total

    def history(self):
        today = self.day_summary()
        today["violations"] = [dict(v) for v in today["violations"]]
        self._annotate(today)
        days = []
        for d in reversed(self.s.get("days") or []):
            rec = dict(d)
            rec["violations"] = [dict(v) for v in d.get("violations") or []]
            self._annotate(rec)
            days.append(rec)
        weeks = self.week_summaries()
        if self.s.get("fines"):
            by_week = {}
            for rec in days + [today]:
                if rec.get("start") is not None:
                    ws = self.week_start_of(rec["start"])
                    by_week[ws] = by_week.get(ws, 0) + (rec.get("fines") or 0)
            for w in weeks:
                w["fines"] = by_week.get(w["start"], 0)
        return {"today": today, "days": days, "weeks": weeks, "fines_on": bool(self.s.get("fines"))}

    def week_summaries(self):
        """Takvim haftası başına özet (en yeni önce): sürüş, gün sayısı, ihlal sayısı, haftalık dinlenme, uzatma/kısa dinlenme sayıları."""
        s = self.s
        local = self.local_abs()
        if not self.weekly_on() or local is None:
            return []
        cur = self.week_start_of(local)
        weeks = {}
        def bucket(ws):
            if ws not in weeks:
                meta = s["week"] if s["week"].get("start") == ws else (s.get("weeks_meta") or {}).get(str(ws), {})
                weeks[ws] = {"start": ws, "drive": 0, "days": 0, "violations": 0, "wrest": meta.get("wrest"),
                             "ext_used": meta.get("ext_used", 0), "red_used": meta.get("red_used", 0)}
            return weeks[ws]
        for d in s.get("days") or []:
            if d.get("start") is None:
                continue
            b = bucket(self.week_start_of(d["start"]))
            b["drive"] += d.get("drive", 0); b["days"] += 1; b["violations"] += len(d.get("violations") or [])
        ts = self._today_start()
        today = self.day_summary()
        if ts is not None and (today["drive"] > 0 or today["segments"]):
            b = bucket(self.week_start_of(ts))
            b["drive"] += today["drive"]; b["days"] += 1; b["violations"] += len(today["violations"])
        bucket(cur)
        out = [weeks[k] for k in sorted(weeks, reverse=True)]
        for w in out:
            w["ago"] = (cur - w["start"]) // WEEK_MIN
            w["limit"] = DRIVE_WEEK
        return out

    def _close_rest_segment(self, kind):
        """Sürüş yeniden başlarken biten dinlenme segmentini sınıflandırır."""
        segs = self.s["day_segments"]
        if not segs or segs[-1]["t"] != "rest" or segs[-1].get("closed"):
            return
        segs[-1]["k"] = kind
        segs[-1]["closed"] = True

    def attribute(self, d):
        """d oyun dakikasını mevcut duruma yazar."""
        s = self.s
        st = s["status"]
        if st == "DRIVING":
            r = s["rest"]
            if not s["rest_daily_done"] and r >= REST_REDUCED and self.red_available():
                self._complete_daily(reduced=True)   # 9 sa dinlendi, 11'i beklemeden yola çıktı: kısa günlük dinlenme
            s["drive_block"] += d
            s["drive_daily"] += d
            kind = "short"
            if r > 0 and s["rest_daily_done"]:
                kind = "break"  # günlük dinlenme zaten tamamlanmıştı, fazlası önemsiz
            elif r >= SPLIT_PART1 and s["split_rest"]:
                s["rest_part1"] = r
                kind = "part1"
                self.log(L("log.rest_part1", r=hm(r), p2=hm(SPLIT_PART2)))
            elif r >= SPLIT_PART1:
                kind = "break"
                self.log(L("log.rest_lost_split_off", r=hm(r)))
            elif s["rest_credited"]:
                kind = "break"  # mola kredilendi (45 ya da 15+30), sessizce
            elif s["split_break"] and BREAK_PART1 <= r < BREAK_MIN and not s["break_part1"]:
                s["break_part1"] = r
                kind = "break1"
                self.log(L("log.break_part1", r=hm(r), p2=hm(BREAK_PART2)))
            elif r >= 5:
                self.log(L("log.rest_lost", r=hm(r)))
            if r > 0:
                self._close_rest_segment(kind)
            s["rest"] = 0
            s["rest_daily_done"] = False
            s["rest_credited"] = False
            s["wrest_kind"] = None
            self._seg_add("drive", d)
            if self.weekly_on():
                self._roll_week()
                if s["last_wrest_end"] is None:
                    local = self.local_abs()
                    s["last_wrest_end"] = (local - d) if local is not None else None   # ilk sürüş: haftalık dinlenme yeni bitmiş sayılır
                if not s["ext_drive"] and s["auto_ext"] and s["week"].get("ext_used", 0) < EXT_PER_WEEK and s["drive_daily"] > DRIVE_DAILY:
                    s["ext_drive"] = s["ext_consumed"] = True   # 9 sa aşıldı: bugün 10 sa, hak harcandı
                    s["week"]["ext_used"] = s["week"].get("ext_used", 0) + 1
                    self.log(L("log.ext_used", n=s["week"]["ext_used"], max=EXT_PER_WEEK))
            self._track_violation()
        elif st == "OFF_DUTY":
            if self.strict_blocked():
                pass   # sıkı mod: motor açık ya da el freni çekili değil → dakika dinlenmeye yazılmaz (görevde gibi)
            else:
                self.add_rest(d)
        # ON_DUTY / YARD_MOVE: sürüş sayılmaz, dinlenme de ilerlemez (ama sıfırlanmaz)
        self.dirty = True

    # ---- tik ----
    # Profile bağlı olmayan (uygulama geneli) ayarlar; geri kalan her şey profil başına tutulur
    SETTING_KEYS = ("mode", "manual_abs", "manual_rate", "on_top", "auto_break", "split_rest", "split_break", "sounds", "onboarded", "hotkey",
                    "mini_pos", "mini_opacity", "offjob_rest", "tz_adjust", "set_time_frame", "win", "lang", "theme", "custom",
                    "weekly_rules", "auto_ext", "auto_red", "profile_key", "spd_km", "spd_min", "strict_rest", "fines", "voice")

    @classmethod
    def counter_keys(cls):
        return [k for k in DEFAULT_STATE if k not in cls.SETTING_KEYS]

    @staticmethod
    def _profile_file(key, hist=False):
        safe = re.sub(r"[^0-9A-Za-z_]", "_", key.replace(":", "_", 1))
        return os.path.join(PROFILES_DIR, safe + (".hist.json" if hist else ".json"))

    def _check_profile(self):
        """Oyun profili değiştiyse sayaçları değiştir: eskisini dosyaya yaz, yenisininkini yükle (yoksa sıfırdan)."""
        p = self.saves.profile
        if not p or p["key"] == self.s.get("profile_key"):
            return
        s = self.s
        old = s.get("profile_key")
        keys = self.counter_keys()
        if old:
            try:
                os.makedirs(PROFILES_DIR, exist_ok=True)
                with open(self._profile_file(old) + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({k: s[k] for k in keys}, f, ensure_ascii=False)
                os.replace(self._profile_file(old) + ".tmp", self._profile_file(old))
                with open(self._profile_file(old, True) + ".tmp", "w", encoding="utf-8") as f:
                    json.dump({"hist": self.hist}, f, ensure_ascii=False)
                os.replace(self._profile_file(old, True) + ".tmp", self._profile_file(old, True))
            except Exception as e:
                log_error("profil sayaçları yazılamadı: %r" % e)
        new = p["key"]
        nf = self._profile_file(new)
        if os.path.exists(nf):
            try:
                with open(nf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                fresh = json.loads(json.dumps(DEFAULT_STATE))
                for k in keys:
                    s[k] = data[k] if k in data else fresh[k]
                s["log"] = data.get("log") or []
                self.hist = []
                try:
                    with open(self._profile_file(new, True), "r", encoding="utf-8") as f:
                        self.hist = [(int(a), sn) for a, sn in json.load(f).get("hist", [])][-HIST_MINUTES:]
                except FileNotFoundError:
                    pass
            except Exception as e:
                log_error("profil sayaçları okunamadı: %r" % e)
        elif old is not None:
            # yeni profil: sayaçlar sıfırdan (ilk tanınan profil mevcut sayaçları devralır: eski kullanıcılar bir şey kaybetmez)
            fresh = json.loads(json.dumps(DEFAULT_STATE))
            for k in keys:
                s[k] = fresh[k]
            s["log"] = []
            self.hist = []
        s["profile_key"] = new
        self.pending_jump = None
        self.auto_yard = None
        self.odo_prev = None
        self.alert_prev = {"rem": None, "rest_left": None}
        self.hist_dirty = True
        self.dirty = True
        self.log(L("log.profile", name=p["name"], lvl=p["level"] if p["level"] is not None else "?"))

    def profile_view(self):
        p = self.saves.profile
        if not p:
            return None
        return {"name": p["name"], "company": p["company"], "level": p["level"], "xp": p["xp"], "key": p["key"], "active": p["key"] == self.s.get("profile_key")}

    # ---- ortalama hız (planlayıcı için) ----
    def _learn_speed(self, d, odo):
        s = self.s
        if odo is None or odo <= 0:
            return
        if self.odo_prev is not None and s["status"] == "DRIVING":
            km = odo - self.odo_prev
            if 0 < km < 3.0 * d:                     # dakikada 3 km'den fazla = ışınlanma/kayıt yükleme, sayma
                s["spd_km"] += km
                s["spd_min"] += d
                if s["spd_min"] > 600:               # son ~10 saatin ağırlığı kalsın
                    s["spd_km"] *= 0.5
                    s["spd_min"] *= 0.5
        self.odo_prev = odo

    def avg_kmh(self):
        s = self.s
        if s["spd_min"] >= 30 and s["spd_km"] > 0:
            return s["spd_km"] / (s["spd_min"] / 60.0)
        return PLAN_DEFAULT_KMH

    # ---- yük planlayıcı ----
    def plan_trip(self, drive_min, deadline_min=None, rest_first=False):
        """drive_min dakikalık sürüşü mevcut haklarla simüle eder: molalar, günlük/haftalık dinlenmeler, toplam süre, pay."""
        s = self.s
        t = 0
        breaks = rests = wrests = 0
        block = DRIVE_BLOCK - s["drive_block"]
        daily = self.daily_limit() - s["drive_daily"] + (60 if self.ext_available() else 0)
        week = None
        wi = self.week_info()
        if wi:
            week = min(DRIVE_WEEK - wi["drive"], DRIVE_FORTNIGHT - wi["fortnight"])
        if s["status"] == "OFF_DUTY" and s["rest"] > 0 and not s["rest_credited"] and s["rest"] < self.break_need():
            pass   # süren mola tamamlanmadan çıkılıyor sayılır (kalan mola yok sayılmaz, sayaçlar olduğu gibi)
        if rest_first:
            need_rest = 0 if s["rest_daily_done"] else max(0, self.daily_need() - s["rest"])
            t += need_rest
            rests += 1 if need_rest > 0 else 0
            block, daily = DRIVE_BLOCK, DRIVE_DAILY + (60 if (self.weekly_on() and s["auto_ext"] and s["week"].get("ext_used", 0) < EXT_PER_WEEK) else 0)
        need = drive_min
        guard = 0
        while need > 0 and guard < 60:
            guard += 1
            lim = min(block, daily) if week is None else min(block, daily, week)
            if lim <= 0:
                if week is not None and week <= 0:
                    t += WREST_MIN; wrests += 1; week = DRIVE_WEEK; block = DRIVE_BLOCK; daily = DRIVE_DAILY
                elif daily <= 0:
                    t += REST_MIN; rests += 1; block = DRIVE_BLOCK; daily = DRIVE_DAILY
                else:
                    t += BREAK_MIN; breaks += 1; block = DRIVE_BLOCK
                continue
            d = min(need, lim)
            need -= d; t += d; block -= d; daily -= d
            if week is not None:
                week -= d
        return {"total": t, "drive": drive_min, "breaks": breaks, "rests": rests, "wrests": wrests,
                "margin": None if deadline_min is None else deadline_min - t}

    def plan_view(self, km=None, hours=None):
        """Planlayıcı çıktısı: rota (telemetri) ya da elle girilen km + teslim penceresi."""
        s = self.s
        src = None
        drive_min = deadline = None
        job_on = bool(self.job and self.job.get("on")) and s["mode"] == "auto" and self.connected
        route_s = getattr(self, "route_s", 0.0) or 0.0
        if km is not None and km > 0:
            src = "manual"
            drive_min = km / self.avg_kmh() * 60.0
            deadline = hours * 60.0 if hours else None
        elif s["mode"] == "auto" and self.connected and route_s > 60:
            src = "route"
            drive_min = route_s / 60.0
            if job_on and self.job.get("delivery") and self.tel_abs:
                deadline = int(self.job["delivery"]) - int(self.tel_abs)
        if drive_min is None:
            return {"available": False, "avg_kmh": round(self.avg_kmh()), "learned": s["spd_min"] >= 30}
        now_plan = self.plan_trip(drive_min, deadline)
        out = {"available": True, "src": src, "drive": round(drive_min), "deadline": None if deadline is None else round(deadline),
               "avg_kmh": round(self.avg_kmh()), "learned": s["spd_min"] >= 30, "now": now_plan,
               "eta_local": None if self.local_abs() is None else self.local_abs() + round(now_plan["total"])}
        if now_plan["rests"] > 0 or now_plan["wrests"] > 0 or (now_plan["margin"] is not None and now_plan["margin"] < 0):
            alt = self.plan_trip(drive_min, deadline, rest_first=True)
            out["rest_first"] = alt
        return out

    PROV_KEYS = ("status", "drive_block", "drive_daily", "rest", "break_credited", "rest_part1", "rest_daily_done", "day_segments",
                 "break_part1", "rest_credited", "day_violations", "ext_drive", "ext_consumed", "week", "last_wrest_end", "wrest_kind")

    HIST_KEYS = PROV_KEYS + ("prov", "last_move_abs")

    def _snapshot(self):
        import copy
        return {k: copy.deepcopy(self.s[k]) for k in self.PROV_KEYS}

    def _hist_load(self):
        try:
            with open(HIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.hist = [(int(a), sn) for a, sn in data.get("hist", [])][-HIST_MINUTES:]
        except FileNotFoundError:
            pass
        except Exception as e:
            log_error("history.json okunamadı: %r" % e)

    def _hist_save(self):
        try:
            tmp = HIST_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"hist": self.hist}, f, ensure_ascii=False)
            os.replace(tmp, HIST_FILE)
            self.hist_dirty = False
        except Exception as e:
            log_error("history.json yazılamadı: %r" % e)

    def _hist_push(self, now):
        """Her oyun dakikası için sayaçların görüntüsünü sakla (kayıt geri yüklenirse dönmek için)."""
        import copy
        if self.hist and self.hist[-1][0] == now:
            return
        if self.hist and self.hist[-1][0] > now:
            self.hist = [(a, x) for (a, x) in self.hist if a < now]
        self.hist.append((now, {k: copy.deepcopy(self.s[k]) for k in self.HIST_KEYS}))
        cutoff = now - HIST_MINUTES
        while self.hist and self.hist[0][0] < cutoff:
            self.hist.pop(0)
        self.hist_dirty = True

    def _rollback(self, now, back):
        """Oyun saati geri gitti (kayıt yüklendi): o ana en yakın görüntüye dön."""
        import copy
        if back < ROLLBACK_MIN:
            return
        found = None
        for abs_t, sn in reversed(self.hist):
            if abs_t <= now:
                found = (abs_t, sn)
                break
        if found is None:
            self.log(L("log.rollback_none", back=hm(back)))
            return
        abs_t, sn = found
        for k in self.HIST_KEYS:
            if k in sn or k in DEFAULT_STATE:   # eski sürüm kayıtlarında yeni alanlar olmayabilir
                self.s[k] = copy.deepcopy(sn[k] if k in sn else DEFAULT_STATE[k])
        self.auto_yard = None
        self.pending_jump = None
        self.hist = [(a, x) for (a, x) in self.hist if a <= now]
        self.hist_dirty = True
        self.dirty = True
        self.log(L("log.rollback", back=hm(back), at=self.clock_text(abs_t + self.tz_offset())))

    def _restore(self, snap):
        import copy
        for k in self.PROV_KEYS:
            self.s[k] = copy.deepcopy(snap[k] if k in snap else DEFAULT_STATE[k])
        self.dirty = True

    def _job_started(self):
        """İş kabul edildi: kamyon sahada → otomatik iç hareket (YARD_MAX_KMH üstünde sürüşe döner)."""
        s = self.s
        self.auto_yard = None
        if s["status"] in ("ON_DUTY", "DRIVING"):
            self.set_status("YARD_MOVE", L("log.job_started_yard"))
            self.auto_yard = "pickup"

    def _job_ended(self):
        self.auto_yard = None

    def _resolve_pending_jump(self):
        """Büyük zaman atlaması: iş olayıyla çakışıyorsa yükleme/boşaltma (sayılmaz), değilse dinlenme."""
        pj = self.pending_jump
        if not pj:
            return
        s = self.s
        d = pj["delta"]
        near_job = self.job_event_at is not None and abs(self.job_event_at - pj["t"]) <= JOB_EVENT_WINDOW
        if near_job:
            # yükleme / boşaltma: hiçbir sayaca yazılmaz
            self.log(L("log.jump_loading", d=hm(d)))
            self.pending_jump = None
            return
        near_travel = self.travel_event_at is not None and abs(self.travel_event_at - pj["t"]) <= JOB_EVENT_WINDOW
        own_skip = self.skip_at is not None and abs(self.skip_at - pj["t"]) <= SKIP_WINDOW
        if not (near_travel or own_skip) and time.monotonic() - pj["t"] < JUMP_HOLD_S:
            return   # iş olayı gelebilir, bekle
        if self.offjob and not s["offjob_rest"]:
            self.log(L("log.jump_offjob", d=hm(d)))
        else:
            self.log(L("log.jump_travel" if near_travel else "log.jump_rest", d=hm(d)))
            self.add_rest(min(d, 24 * 60))
            if s["status"] == "DRIVING":
                s["status"] = "ON_DUTY"
        self.pending_jump = None

    def tick_auto(self):
        tel = self.tel.read()
        was = self.connected
        self.tel_ok = bool(tel and tel["active"])
        if tel:
            self.speed = tel["speed_kmh"]
            self.paused = tel["paused"]
            self.game = tel["game"]
            self.saves.game = self.game or 1
            self.tel_rev = tel["revision"]
            self.tel_scale = tel["scale"]
            self.truck = tel["truck"]
            self.job = tel["job"]
            self.route_m = tel.get("route_m", 0.0)
            self.route_s = tel.get("route_s", 0.0)
            self.park_brake = bool(tel.get("park_brake"))
            self.engine = bool(tel.get("engine"))
        self.connected = self.tel_ok and tel["time_abs"] > 0
        if not self.connected:
            return
        self.tel_abs = now = tel["time_abs"]
        self._check_profile()
        s = self.s
        job_on = bool(self.job and self.job.get("on"))
        job_start = self.job.get("start") if self.job else None
        loaded = bool(self.job and self.job.get("loaded"))
        ferry, train = bool(tel.get("ferry")), bool(tel.get("train"))
        if not was or s["last_abs"] is None:
            # yeni bağlantı: saati eşitle, aradaki süreyi sayma
            s["last_abs"] = now
            s["last_move_abs"] = now
            self.prev_job_on, self.prev_job_start, self.prev_loaded = job_on, job_start, loaded
            self.prev_ferry, self.prev_train = ferry, train
            self.log(L("log.connected"))
            return

        # --- iş olayları (yükleme atlaması ve otomatik iç hareket için) ---
        mono = time.monotonic()
        if self.prev_job_start is not None and job_start != self.prev_job_start:
            self.job_event_at = mono
        if self.prev_job_on is not None and job_on != self.prev_job_on:
            self.job_event_at = mono
            if job_on:
                self._job_started()
            else:
                self._job_ended()
        if self.prev_loaded is not None and loaded != self.prev_loaded:
            # yükleme (0→1) / boşaltma (1→0): bu ana denk gelen zaman atlaması sayaçlara yazılmaz
            self.job_event_at = mono
            self.log(L("log.cargo_loaded") if loaded else L("log.cargo_unloaded"))
        if self.prev_ferry is not None and ferry != self.prev_ferry:
            self.travel_event_at = mono
            self.log(L("log.ferry"))
        if self.prev_train is not None and train != self.prev_train:
            self.travel_event_at = mono
            self.log(L("log.train"))
        self.prev_job_on, self.prev_job_start, self.prev_loaded = job_on, job_start, loaded
        self.prev_ferry, self.prev_train = ferry, train

        moving = self.speed > MOVE_KMH and not self.paused
        route = self.route_m
        delta = now - s["last_abs"]

        # --- büyük zaman atlaması: karar bekletilir (yükleme mi, dinlenme mi) ---
        if delta >= JUMP_MIN:
            self.pending_jump = {"delta": delta, "t": mono}
            delta = 0
        elif delta < 0:
            self._rollback(now, -delta)
            s["last_abs"] = now
            self.prev_job_on, self.prev_job_start, self.prev_loaded = job_on, job_start, loaded
            self.prev_ferry, self.prev_train = ferry, train
            return
        self._resolve_pending_jump()

        # --- geçici sayım: iş yok ama GPS rotası var ---
        provisional = (not job_on) and route > 0
        if s["prov"] is None and provisional:
            s["prov"] = {"snap": self._snapshot(), "abs": now}
            self.log(L("log.prov_start"))
            self.dirty = True
        elif s["prov"] is not None:
            if job_on:
                self.log(L("log.prov_commit"))
                s["prov"] = None
                self.dirty = True
            elif route <= 0:
                elapsed = now - s["prov"]["abs"]
                self._restore(s["prov"]["snap"])
                s["prov"] = None
                s["last_move_abs"] = now
                self.pending_jump = None
                self.log(L("log.prov_revert", d=hm(max(elapsed, 0))))

        # --- görev dışı: iş yok ve rota yok ---
        offjob = (not job_on) and route <= 0
        if offjob != self.offjob:
            self.offjob = offjob
            if offjob:
                self.log(L("log.offjob_on") + (L("log.offjob_on_rest") if s["offjob_rest"] else ""))
            else:
                self.log(L("log.offjob_off_job") if job_on else L("log.offjob_off_route"))
        if offjob:
            if s["offjob_rest"]:
                if moving and s["status"] == "OFF_DUTY":
                    if s["rest"] >= 5:
                        self.log(L("log.offjob_rest_cut", r=hm(s["rest"])))
                    s["rest"] = 0
                    s["rest_daily_done"] = False
                    self.set_status("ON_DUTY")
                if delta > 0 and s["status"] == "OFF_DUTY":
                    self.add_rest(delta)
            if s["status"] in ("DRIVING", "YARD_MOVE"):
                s["status"] = "ON_DUTY"
                self.auto_yard = None
            if moving:
                s["last_move_abs"] = now
            s["last_abs"] = now
            self._hist_push(now)
            return

        # --- hareketten durum ---
        if moving:
            self.no_auto_break = False
        if s["status"] == "YARD_MOVE":
            if self.speed > YARD_MAX_KMH and not self.paused:
                self.set_status("DRIVING", L("log.yard_speed", kmh=f"{YARD_MAX_KMH:.0f}"))
                s["last_move_abs"] = now
                self.auto_yard = None
            elif self.auto_yard == "delivery" and route > DELIVERY_FAR_M:
                self.set_status("ON_DUTY", L("log.delivery_left"))
                self.auto_yard = None
        else:
            self.auto_yard = None
            if (job_on and route <= DELIVERY_NEAR_M and self.speed < DELIVERY_MAX_KMH and not self.paused
                    and s["status"] in ("ON_DUTY", "DRIVING")):
                self.set_status("YARD_MOVE", L("log.delivery_yard", kmh=f"{YARD_MAX_KMH:.0f}"))
                self.auto_yard = "delivery"
            elif moving:
                self.set_status("DRIVING")
                s["last_move_abs"] = now
            elif s["status"] == "DRIVING" and s["last_move_abs"] is not None and now - s["last_move_abs"] > DRIVE_GRACE:
                self.set_status("ON_DUTY", L("log.stopped"))
            elif (s["status"] == "ON_DUTY" and s["auto_break"] and not self.no_auto_break and not self.paused
                  and self.remaining() <= AUTO_BREAK_REMAINING
                  and s["last_move_abs"] is not None and now - s["last_move_abs"] >= AUTO_BREAK_STOPPED):
                rem = self.remaining()
                self.set_status("OFF_DUTY", L("log.auto_break", rem=hm(max(rem, 0)), min=AUTO_BREAK_STOPPED))
        if moving and s["status"] == "YARD_MOVE":
            s["last_move_abs"] = now

        # --- zamanı dağıt ---
        if delta > 0:
            self.attribute(delta)
            self._learn_speed(delta, tel.get("odometer_km"))
        s["last_abs"] = now
        self._hist_push(now)

    def tick_manual(self, dt):
        # telemetriyi yine oku ki "oyun bulundu" gösterebilelim
        tel = self.tel.read()
        self.tel_ok = bool(tel and tel["active"])
        if tel:
            self.speed = tel["speed_kmh"]
            self.paused = tel["paused"]
            self.game = tel["game"]
            self.saves.game = self.game or 1
            self.tel_abs = tel["time_abs"] if tel["time_abs"] > 0 else None
            self.truck = tel["truck"]
            self.job = tel["job"]
        self.connected = False
        s = self.s
        rate = max(0.0, float(s["manual_rate"]))
        s["manual_abs"] += rate * dt / 60.0
        now = int(s["manual_abs"])
        if s["last_abs"] is None:
            s["last_abs"] = now
            return
        delta = now - s["last_abs"]
        if delta > 0:
            self.attribute(delta)
        s["last_abs"] = now

    def run(self):
        self.saves.start()
        last = time.monotonic()
        while not self.stop:
            now = time.monotonic()
            dt, last = now - last, now
            try:
                with self.lock:
                    if self.s["mode"] == "auto":
                        self.tick_auto()
                    else:
                        self.tick_manual(dt)
                    self._check_alerts()
                self.maybe_save()
            except Exception:
                log_error(traceback.format_exc())
            time.sleep(TICK)

    # ---- atlama (oyun konsolu) ----
    def skip_info(self):
        """Atla butonu için: kaç dk atlanacak, buton aktif mi."""
        s = self.s
        if s["mode"] != "auto" or not self.connected or s["status"] != "OFF_DUTY":
            return {"available": False}
        if self.offjob and not s["offjob_rest"]:
            return {"available": False}
        target = self.rest_target()
        need = target - s["rest"]
        if need <= 0:
            return {"available": False}
        return {
            "available": True,
            "busy": self.skip is not None,
            "blocked": self.skip_info_blockers(),
            "minutes": need + SKIP_MARGIN,
            "label": L("skip.break") if target in (BREAK_MIN, BREAK_PART2) else (L("skip.part2") if self.split_active() else L("skip.daily")),
        }

    def skip_info_blockers(self):
        """Atla / tam dinlenme için ortak engeller (duraklatılmış, konsol kapalı, dilim farkı belirsiz)."""
        s = self.s
        if self.paused:
            return L("skip.blocked_paused")
        if self.saves.console_ok is False:
            return L("skip.blocked_console")
        if s["set_time_frame"] == "local" and self.saves.zones_enabled and self.game != 2:
            # komut HUD saatini aldığı için dilim farkı güncel olmalı; yoksa 23 saat ileri fırlayabilir
            if self.saves.tz is None:
                return L("skip.blocked_tz")
            if self.saves.save_time is not None and self.tel_abs - self.saves.save_time > SAVE_FRESH_MIN:
                return L("skip.blocked_stale")
        return None

    def full_rest_info(self):
        """Araç dururken ana paneldeki 'tam dinlenme' düğmesi: günlük dinlenmeyi (11 sa / 2. kısım 9 sa) tek seferde atlar."""
        s = self.s
        if s["mode"] != "auto" or not self.connected or self.speed > MOVE_KMH or s["rest_daily_done"]:
            return {"available": False}   # durum DRIVING kalsa da (kısa duruş) araç duruyorsa gösterilir
            return {"available": False}
        if self.offjob and not s["offjob_rest"]:
            return {"available": False}
        need = self.daily_need() - s["rest"]
        if need <= 0:
            return {"available": False}
        return {"available": True, "busy": self.skip is not None, "blocked": self.skip_info_blockers(), "minutes": need + SKIP_MARGIN,
                "label": L("fullrest.part2", d=hm(self.daily_need())) if self.split_active() else L("fullrest.label", d=hm(self.daily_need()))}

    def wrest_info(self):
        """Ana paneldeki 'Haftalık dinlenme (45 sa)' düğmesi (yalnızca haftalık kurallar açıkken, araç dururken)."""
        s = self.s
        if not self.weekly_on() or s["mode"] != "auto" or not self.connected or self.speed > MOVE_KMH:
            return {"available": False}
        if self.offjob and not s["offjob_rest"]:
            return {"available": False}
        if s["wrest_kind"] == "regular":
            return {"available": False}
        need = WREST_MIN - s["rest"]
        if need <= 0:
            return {"available": False}
        return {"available": True, "busy": self.skip is not None, "blocked": self.skip_info_blockers(),
                "minutes": need + SKIP_MARGIN, "label": L("wrest.label", d=hm(WREST_MIN))}

    def act_weekly_rest(self):
        with self.lock:
            info = self.wrest_info()
            if not info["available"] or info["busy"] or info["blocked"]:
                return
            if self.s["status"] != "OFF_DUTY":
                self.act_set_status_locked("OFF_DUTY")
            self._start_skip(info["minutes"])
        threading.Thread(target=self._skip_worker, daemon=True).start()

    def act_set_weekly_rules(self, flag):
        with self.lock:
            s = self.s
            flag = bool(flag)
            if flag == bool(s["weekly_rules"]):
                return
            s["weekly_rules"] = flag
            if flag:
                self._roll_week()
                if s["last_wrest_end"] is None:
                    s["last_wrest_end"] = self.local_abs()
            else:
                s["ext_drive"] = s["ext_consumed"] = False
            self.dirty = True
            self.log(L("log.weekly_on" if flag else "log.weekly_off"))

    def act_set_auto_ext(self, flag):
        with self.lock:
            s = self.s
            flag = bool(flag)
            if flag == bool(s["auto_ext"]):
                return
            s["auto_ext"] = flag
            self.dirty = True
            self.log(L("log.auto_ext_on" if flag else "log.auto_ext_off"))

    def act_set_flag(self, key, flag):
        with self.lock:
            if key not in ("strict_rest", "fines", "voice"):
                return
            flag = bool(flag)
            if flag == bool(self.s[key]):
                return
            self.s[key] = flag
            self.dirty = True
            self.log(L("log.%s_%s" % (key, "on" if flag else "off")))

    def act_set_auto_red(self, flag):
        with self.lock:
            s = self.s
            flag = bool(flag)
            if flag == bool(s["auto_red"]):
                return
            s["auto_red"] = flag
            self.dirty = True
            self.log(L("log.auto_red_on" if flag else "log.auto_red_off"))

    def red_rest_info(self):
        """Ana paneldeki 'Kısa dinlenme (9 sa)' çipi: otomatik kısa dinlenme açık, hak var, araç duruyor."""
        s = self.s
        if not self.red_available() or s["mode"] != "auto" or not self.connected or self.speed > MOVE_KMH:
            return {"available": False}
        if s["rest_daily_done"] or self.split_active() or s["rest"] >= REST_REDUCED:
            return {"available": False}
        if self.offjob and not s["offjob_rest"]:
            return {"available": False}
        need = REST_REDUCED - s["rest"]
        return {"available": True, "busy": self.skip is not None, "blocked": self.skip_info_blockers(), "minutes": need + SKIP_MARGIN,
                "label": L("redrest.label", d=hm(REST_REDUCED)), "n": s["week"].get("red_used", 0) + 1, "max": RED_PER_WEEK}

    def act_red_rest(self):
        with self.lock:
            info = self.red_rest_info()
            if not info["available"] or info["busy"] or info["blocked"]:
                return
            if self.s["status"] != "OFF_DUTY":
                self.act_set_status_locked("OFF_DUTY")
            self._start_skip(info["minutes"])
        threading.Thread(target=self._skip_worker, daemon=True).start()

    def act_full_rest(self):
        with self.lock:
            info = self.full_rest_info()
            if not info["available"] or info["busy"] or info["blocked"]:
                return
            if self.s["status"] != "OFF_DUTY":
                self.act_set_status_locked("OFF_DUTY")
            self._start_skip(info["minutes"])
        threading.Thread(target=self._skip_worker, daemon=True).start()

    def _start_skip(self, need):
        """Atlama işini başlatır (kilit tutulmuş olmalı). g_set_time günün saatini kurduğu için 23 saatten uzun
        atlamalar (haftalık dinlenme) art arda birkaç komutla yapılır."""
        chunks = []
        left = need
        while left > 0:
            c = min(left, SKIP_CHUNK_MAX)
            chunks.append(c)
            left -= c
        self.skip = {"chunks": chunks, "need": need, "start_abs": self.tel_abs, "t0": time.monotonic()}
        self.skip_at = time.monotonic()
        self.log(L("log.skip_sending", d=hm(need), cmd=self._skip_cmd(chunks[0])) + (L("log.skip_steps", n=len(chunks)) if len(chunks) > 1 else ""))

    def _skip_cmd(self, delta):
        base_now = self.tel_abs
        frame_now = base_now if self.s["set_time_frame"] == "base" else base_now + self.tz_offset()
        target = frame_now + delta
        return f"g_set_time {(target % 1440) // 60} {target % 60}"

    def act_skip_rest(self):
        with self.lock:
            info = self.skip_info()
            if not info["available"] or info["busy"] or info["blocked"]:
                return
            self._start_skip(info["minutes"])
        threading.Thread(target=self._skip_worker, daemon=True).start()

    def _skip_worker(self):
        sk = self.skip
        try:
            total = 0
            for i, chunk in enumerate(sk["chunks"]):
                with self.lock:
                    base = self.tel_abs
                    cmd = self._skip_cmd(chunk)
                    self.skip_at = time.monotonic()
                ok, err = self.console.send_command(cmd)
                if not ok:
                    with self.lock:
                        self.log(L("log.skip_failed", err=err))
                    return
                # oyun saati değişsin diye bekle (duraklatılmışsa devam edince görünür)
                t0 = time.monotonic()
                while time.monotonic() - t0 < 8 and self.tel_abs == base:
                    time.sleep(0.2)
                time.sleep(0.5)
                delta = (self.tel_abs or base) - base
                if delta == 0:
                    with self.lock:
                        self.log(L("log.skip_nochange"))
                    return
                total += delta
                if i < len(sk["chunks"]) - 1:
                    time.sleep(1.5)   # sayaçlar bu adımı işlesin, sonra sıradaki adım
            with self.lock:
                if abs(total - sk["need"]) <= 2 * len(sk["chunks"]):
                    self.log(L("log.skip_ok", d=hm(total)))
                else:
                    self.log(L("log.skip_mismatch", need=hm(sk["need"]), d=hm(total)))
        except Exception:
            log_error(traceback.format_exc())
        finally:
            self.skip = None

    # ---- akış kutucukları ----
    def sequence(self):
        """Günün akışını kutucuk listesi olarak kurar: geçmiş (✓), şimdiki (aktif), plan.
        Kutucuk: {"k": drive|break|part1|daily, "v": "S:DD", "st": done|active|plan, "n": "1/2"|"2/2"|None}"""
        s = self.s
        split_on = bool(s["split_rest"])
        resting = s["status"] == "OFF_DUTY"
        boxes = []
        drive_acc = 0
        first = True
        segs = s["day_segments"]
        last_open_rest = segs[-1] if (segs and segs[-1]["t"] == "rest" and not segs[-1].get("closed")) else None
        block_used_before = 0   # bu blokta 1. parça moladan önce sürülen (bölünmüş mola)
        for seg in segs:
            if seg is last_open_rest:
                break
            if seg["t"] == "drive":
                drive_acc += seg["m"]; first = False
            else:
                if first:
                    continue  # gün başındaki dinlenme kalıntısı
                k = seg.get("k", "short")
                if k == "part1" and not split_on:
                    k = "break"
                if k == "short":
                    continue  # kısa duruş: blok devam ediyor
                boxes.append({"k": "drive", "v": hm(drive_acc), "st": "done"})
                if k == "break1":
                    block_used_before = drive_acc
                else:
                    block_used_before = 0
                drive_acc = 0
                boxes.append({"k": k, "v": hm(seg["m"]), "st": "done", "n": "1/2" if k == "part1" else None})
        part1_seen = any(b["k"] == "part1" for b in boxes)
        break_seen = any(b["k"] == "break" for b in boxes)
        need = SPLIT_PART2 if (split_on and (part1_seen or s["rest_part1"] >= SPLIT_PART1)) else REST_MIN
        bn = self.break_need()
        rem_daily = self.daily_limit() - s["drive_daily"]
        rest = s["rest"]
        credited = s["rest_credited"]

        if resting or (last_open_rest and rest > 0):
            # önceki sürüş bloğu (kısa duruşlarla birleşik)
            if drive_acc > 0:
                boxes.append({"k": "drive", "v": hm(drive_acc), "st": "done" if credited else "part"})
            if s["rest_daily_done"] or rest >= need:
                boxes.append({"k": "daily", "v": hm(need), "st": "done", "n": "2/2" if need == SPLIT_PART2 else None})
            elif not credited and self.rest_target() == bn:
                boxes.append({"k": "break", "v": hm(bn), "st": "active"})
                if rem_daily > 0:
                    boxes.append({"k": "drive", "v": hm(min(DRIVE_BLOCK, rem_daily)), "st": "plan"})
                boxes.append({"k": "daily", "v": hm(need), "st": "plan", "n": "2/2" if need == SPLIT_PART2 else None})
            elif split_on and rest >= SPLIT_PART1 and not part1_seen:
                # şu an 1. kısım olabilecek bir dinlenme: sürerse 11 sa, kalkarsa 3+9
                boxes.append({"k": "part1", "v": hm(rest), "st": "active", "n": "1/2"})
                if rem_daily > 0:
                    boxes.append({"k": "drive", "v": hm(min(DRIVE_BLOCK, rem_daily)), "st": "plan"})
                boxes.append({"k": "daily", "v": hm(SPLIT_PART2), "st": "plan", "n": "2/2"})
            elif credited and rem_daily > 0:
                # mola tamamlandı, dinlenme sürüyor: kalkarsa kalan sürüş hakkı ve günlük dinlenme
                boxes.append({"k": "break", "v": hm(rest), "st": "done"})
                boxes.append({"k": "drive", "v": hm(min(DRIVE_BLOCK, rem_daily)), "st": "plan"})
                boxes.append({"k": "daily", "v": hm(need), "st": "plan", "n": "2/2" if need == SPLIT_PART2 else None})
            else:
                boxes.append({"k": "daily", "v": hm(need), "st": "active", "n": "2/2" if need == SPLIT_PART2 else None})
        else:
            before_block = s["drive_daily"] - s["drive_block"]
            limit = max(0, min(DRIVE_BLOCK, self.daily_limit() - before_block))
            boxes.append({"k": "drive", "v": hm(max(0, limit - block_used_before)), "st": "active"})
            after = self.daily_limit() - (before_block + limit)
            if not break_seen and not s["break_credited"]:
                boxes.append({"k": "break", "v": hm(bn), "st": "plan"})
                if after > 0:
                    boxes.append({"k": "drive", "v": hm(min(DRIVE_BLOCK, after)), "st": "plan"})
            boxes.append({"k": "daily", "v": hm(need), "st": "plan", "n": "2/2" if need == SPLIT_PART2 else None})
        return boxes

    # ---- sesli uyarılar ----
    def _sound(self, name):
        """assets/sounds/<name>.wav dosyasını (varsa) eşzamansız çalar."""
        if not self.s.get("sounds", True):
            return
        path = os.path.join(SOUND_DIR, name + ".wav")
        if not os.path.exists(path):
            return
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as e:
            log_error("ses çalınamadı: %r" % e)

    def _check_alerts(self):
        """Sürüş bitimine 15 dk kala / bittiği an ve dinlenme bitimine 15 dk kala uyarı sesi."""
        s = self.s
        prev = self.alert_prev
        rem = self.remaining()
        active = s["status"] != "OFF_DUTY" and not (self.offjob and s["mode"] == "auto")
        if active and prev["rem"] is not None:
            if prev["rem"] > ALERT_BEFORE >= rem > 0:
                self._sound("warn")
                self.log(L("log.snd_drive15", d=hm(rem)))
                self._voice("drive15", d=ALERT_BEFORE)
            elif prev["rem"] > 0 >= rem:
                self._sound("alert")
                self.log(L("log.snd_drive0"))
                self._voice("drive0")
        prev["rem"] = rem if active else None
        if s["status"] == "OFF_DUTY":
            target = self.daily_need() if s["rest_credited"] else self.rest_target()
            left = target - s["rest"]
            if prev["rest_left"] is not None and prev["rest_left"] > ALERT_BEFORE >= left > 0:
                self._sound("warn")
                self.log(L("log.snd_rest15", d=hm(left)))
                self._voice("rest15", d=ALERT_BEFORE)
            prev["rest_left"] = left
        else:
            prev["rest_left"] = None

    # ---- mini şerit / kısayol ----
    def act_toggle_mini(self):
        """Mini şeridi aç/kapat. Şerit her açılışta yeniden oluşturulur: WebView2'nin saydam arka planı yalnızca
        taze bir pencerede güvenilir (gizle/göster ya da ekran değişimleri sonrası köşeler kararabiliyor)."""
        try:
            with self.mini_lock:   # yalnızca referans değişimi kilitli; destroy() UI iş parçacığını bekler, kilit tutulmaz
                win, self.mini_win, self.mini_on = self.mini_win, None, False
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
                if self.window is not None:
                    self.window.show()
                return
            pos = self.s.get("mini_pos") or {}
            kw = {}
            if "x" in pos and "y" in pos and rect_on_screen(pos["x"], pos["y"], MINI_W, MINI_H):
                kw = {"x": int(pos["x"]), "y": int(pos["y"])}
            theme = self.s.get("theme") or DEFAULT_THEME
            bg = {"dark": "#171b22", "light": "#ffffff"}.get(theme, "#0b1230")
            if theme == "custom":
                bg = _mix_hex(self.s["custom"]["win"], "#000000", 0.35)
            win = webview.create_window(WINDOW_TITLE + " Mini", OVERLAY_FILE + "#" + theme_hash(self.s), js_api=self.api,
                                        width=MINI_W, height=MINI_H,
                                        min_size=(300, 60), frameless=True, easy_drag=True, on_top=True, resizable=False,
                                        background_color=bg, **kw)
            # pencere var ama henüz gösterilmedi: görev çubuğunda yer almasın; saydamlık pencere alfasıyla
            set_toolwindow(win)
            set_window_alpha(win, self.s.get("mini_opacity", 0.7))
            # WinForms boyutu çerçeve kaldırılmadan önce uygular (16 px eksik kalır); tam boyuta getir.
            # Pencere create_window içinde eşzamanlı gösterildiği için "shown" olayına bağlanmak geç kalır.
            try:
                win.resize(MINI_W, MINI_H)
            except Exception:
                pass

            def shown():
                try:
                    win.resize(MINI_W, MINI_H)
                except Exception:
                    pass

            def moved(x, y):
                with self.lock:
                    self.s["mini_pos"] = {"x": x, "y": y}
                    self.dirty = True

            def closing():
                # kullanıcı şeridi kapattı (Alt+F4 vb.): pencere gitsin, ana pencere geri gelsin.
                # (Kilit alınmaz: destroy() çağıran iş parçacığı kilidi tutuyorsa UI kilitlenir.)
                if self.exiting or self.mini_win is not win:
                    return True   # çıkış ya da bizim destroy() çağrımız — ana pencereyi çağıran gösterir
                self.mini_win, self.mini_on = None, False
                if self.window is not None:
                    self.window.show()
                return True

            win.events.shown += shown
            if hasattr(win.events, "moved"):
                win.events.moved += moved
            win.events.closing += closing
            self.mini_win = win
            self.mini_on = True
            if self.window is not None:
                self.window.hide()
        except Exception:
            log_error(traceback.format_exc())

    def act_set_hotkey(self, mods, vk, name):
        with self.lock:
            self.s["hotkey"] = {"mods": int(mods), "vk": int(vk), "name": str(name)[:40]}
            self.dirty = True
        if self.hotkeys is not None:
            self.hotkeys.set(int(mods), int(vk))

    def act_set_sounds(self, flag):
        with self.lock:
            self.s["sounds"] = bool(flag)
            self.dirty = True

    def act_win(self, what):
        """Başlık çubuğu düğmeleri: min | max | close."""
        win = self.window
        if win is None:
            return
        if what == "min":
            window_syscommand(win, 0xF020)
        elif what == "max":
            hwnd = _hwnd_of(win)
            if not hwnd:
                return
            u = _user32()
            SWP = 0x0004 | 0x0010   # NOZORDER | NOACTIVATE
            wr = wt.RECT(); u.GetWindowRect(hwnd, ctypes.byref(wr))
            if not self.win_maxed:
                self.win_normal = (wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top)
                wa = work_area(hwnd)   # görev çubuğu hariç
                u.SetWindowPos(hwnd, None, wa.left, wa.top, wa.right - wa.left, wa.bottom - wa.top, SWP)
                self.win_maxed = True
            else:
                x, y, w, h = self.win_normal or (wr.left, wr.top, 460, 950)
                u.SetWindowPos(hwnd, None, x, y, w, h, SWP)
                self.win_maxed = False
        elif what == "close":
            # destroy() JS köprüsü iş parçacığından çağrılırsa köprü yanıtı beklerken takılıyor; WM_CLOSE gönder,
            # kapanış UI iş parçacığında olur ve closing olayı durumu kaydeder
            hwnd = _hwnd_of(win)
            if hwnd:
                _user32().PostMessageW(hwnd, 0x0010, 0, 0)

    def act_win_resize(self, edge, dx, dy):
        """Sayfadaki kenar tutamacı sürüklenirken: edge = n|s|e|w|ne|nw|se|sw; dx/dy = sürükleme başından fark.
        edge None ise başlangıç dikdörtgeni kaydedilir."""
        win = self.window
        hwnd = _hwnd_of(win) if win is not None else None
        if not hwnd or self.win_maxed:
            return
        u = _user32()
        if edge is None:
            r = wt.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
            self.win_drag0 = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            return
        if not getattr(self, "win_drag0", None):
            return
        x, y, w, h = self.win_drag0
        dx, dy = int(dx), int(dy)
        MINW, MINH = 380, 600
        if "e" in edge:
            w = max(MINW, w + dx)
        if "s" in edge:
            h = max(MINH, h + dy)
        if "w" in edge:
            nw = max(MINW, w - dx); x += w - nw; w = nw
        if "n" in edge:
            nh = max(MINH, h - dy); y += h - nh; h = nh
        u.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010)   # NOZORDER | NOACTIVATE

    def act_set_mini_opacity(self, value):
        with self.lock:
            self.s["mini_opacity"] = max(0.3, min(1.0, float(value)))
            self.dirty = True
        win = self.mini_win
        if win is not None:
            set_window_alpha(win, self.s["mini_opacity"], rounded=False)   # açıkken canlı uygula

    def act_set_onboarded(self, flag):
        with self.lock:
            self.s["onboarded"] = bool(flag)
            self.dirty = True

    def act_set_split_break(self, flag):
        with self.lock:
            self.s["split_break"] = bool(flag)
            if not flag:
                self.s["break_part1"] = 0
            self.dirty = True

    def weekly_view(self, wi, local):
        s = self.s
        if not self.weekly_on():
            return {"on": False}
        w = s["week"]
        out = {"on": True, "drive": wi["drive"] if wi else 0, "fortnight": wi["fortnight"] if wi else 0,
               "week_limit": DRIVE_WEEK, "fortnight_limit": DRIVE_FORTNIGHT,
               "rest_deadline": self.rest_deadline(), "wrest_due": self.wrest_due(), "wrest_kind": s["wrest_kind"],
               "ext": {"auto": bool(s["auto_ext"]), "on": bool(s["ext_drive"]), "bonus": bool(s["ext_drive"]) or self.ext_available(), "used": w.get("ext_used", 0), "max": EXT_PER_WEEK},
               "red": {"auto": bool(s["auto_red"]), "used": w.get("red_used", 0), "max": RED_PER_WEEK},
               "local": local}
        rd = out["rest_deadline"]
        out["day_left"] = None if (rd is None or local is None) else rd - local
        return out

    def flags(self):
        """Ana göstergede metin yerine ikonla gösterilecek durum bayrakları."""
        s = self.s
        f = []
        if s["mode"] == "auto" and self.connected and self.paused:
            f.append({"ic": "pause", "t": L("flag.paused")})
        if self.offjob and s["mode"] == "auto" and self.connected:
            f.append({"ic": "nojob", "t": L("flag.nojob")})
            f.append({"ic": "timer-off", "t": L("flag.rest_counts") if s["offjob_rest"] else L("flag.frozen")})
            return f
        if s["prov"] and s["mode"] == "auto" and self.connected:
            f.append({"ic": "road", "t": L("flag.prov")})
        if s["status"] == "ON_DUTY":
            f.append({"ic": "parked", "t": L("flag.parked")})
            f.append({"ic": "timer-off", "t": L("flag.timer_off")})
        elif s["status"] == "YARD_MOVE":
            f.append({"ic": "yard", "t": {"pickup": L("flag.yard_pickup"), "delivery": L("flag.yard_delivery")}.get(self.auto_yard, L("flag.yard"))})
            f.append({"ic": "timer-off", "t": L("flag.not_driving")})
        if s["status"] == "OFF_DUTY" and self.strict_blocked():
            f.append({"ic": "engine", "t": L("flag.strict_engine") if self.engine else L("flag.strict_brake"), "warn": True})
        if self.weekly_on() and s["status"] != "OFF_DUTY":
            rd, local = self.rest_deadline(), self.local_abs()
            if rd is not None and local is not None:
                left = rd - local
                if left >= 0:
                    f.append({"ic": "clock", "t": L("flag.day_end", t=self.clock_text(rd), r=hm(left)), "warn": left <= 60})
                else:
                    f.append({"ic": "clock", "t": L("flag.day_end_over", r=hm(-left)), "bad": True})
        return f

    # ---- görünüm ----
    def view(self):
        s = self.s
        abs_now = self.current_abs()
        off = self.tz_offset()
        local = None if abs_now is None else abs_now + off
        self._roll_week()
        rem_block = DRIVE_BLOCK - s["drive_block"]
        rem_daily = self.daily_limit() - s["drive_daily"]
        wi = self.week_info()
        rem_week = min(DRIVE_WEEK - wi["drive"], DRIVE_FORTNIGHT - wi["fortnight"]) if wi else None
        remaining = min(rem_block, rem_daily) if rem_week is None else min(rem_block, rem_daily, rem_week)
        if rem_week is not None and rem_week <= rem_daily and rem_week <= rem_block:
            next_req = "WREST"
        else:
            next_req = "REST" if rem_daily <= rem_block else "BREAK"
        need_txt = L("need.part2", d=hm(self.daily_need())) if self.split_active() else L("need.daily")
        next_txt = {"WREST": L("need.wrest"), "REST": need_txt}.get(next_req, L("need.break"))
        rest = s["rest"]
        status = s["status"]
        target = self.rest_target()
        need = self.daily_need()
        split = self.split_active()
        daily_lbl = L("big.daily_part2") if split else L("big.daily")
        split_note = f" · 1. kısım {hm(s['rest_part1'])} ✓" if split else ""

        tone = "ok"
        if status == "OFF_DUTY":
            tone = "rest"
            if s["rest_daily_done"] or rest >= need:
                label, value = L("big.daily_done"), hm(rest)
                sub = L("big.daily_done_sub")
            elif rest >= REST_REDUCED and self.red_available():
                label, value = L("big.red_ready"), f"{hm(rest)} / {hm(need)}"
                sub = L("big.red_ready_sub", n=s["week"].get("red_used", 0) + 1, max=RED_PER_WEEK, d=hm(need))
            elif s["rest_credited"]:
                value = f"{hm(rest)} / {hm(need)}"
                if remaining > 0:
                    label = L("big.break_done")
                    sub = L("big.break_done_sub", rem=hm(remaining), next=next_txt)
                else:
                    label = daily_lbl
                    sub = L("big.then_drive_full")
            elif target in (BREAK_MIN, BREAK_PART2):
                label = L("big.break_part2") if target == BREAK_PART2 else L("big.break")
                value = f"{hm(rest)} / {hm(target)}"
                sub = L("big.then_drive", d=hm(min(DRIVE_BLOCK, rem_daily)))
            else:
                label, value = daily_lbl, f"{hm(rest)} / {hm(need)}"
                sub = L("big.daily_sub_rem", rem=hm(remaining)) if remaining > 0 else L("big.then_drive_full")
            seq = 3 if (s["rest_credited"] or target not in (BREAK_MIN, BREAK_PART2)) else 1
        else:
            if remaining < 0:
                tone = "bad"
                label, value = L("big.violation"), "+" + hm(-remaining)
                sub = L("big.violation_sub", next=next_txt)
            else:
                tone = "warn" if remaining <= 15 else "ok"
                label, value = L("big.remaining"), hm(remaining)
                sub = L("big.then", next=next_txt)
            seq = 2 if s["break_credited"] else 0

        if s["mode"] == "manual":
            conn = {"state": "manual", "text": L("conn.manual", rate=f"{s['manual_rate']:g}")}
            if self.tel_ok:
                conn["text"] += L("conn.manual_game_found")
        elif self.connected:
            gname = {1: "ETS2", 2: "ATS"}.get(self.game, L("conn.game"))
            conn_text = self.truck or (L("conn.paused", game=gname) if self.paused else L("conn.connected", game=gname))
            conn = {"state": "paused" if self.paused else "ok", "text": conn_text}
        elif self.tel_ok:
            conn = {"state": "wait", "text": L("conn.wait_profile")}
        else:
            conn = {"state": "off", "text": L("conn.waiting")}

        job = None
        if self.job and self.job.get("on") and s["mode"] == "auto" and self.connected and (self.job.get("src") or self.job.get("dst")):
            due = None
            if self.job.get("delivery") and self.tel_abs:
                due = int(self.job["delivery"]) - int(self.tel_abs)
            job = {"src": self.job.get("src", ""), "dst": self.job.get("dst", ""), "cargo": self.job.get("cargo", ""),
                   "remaining": due, "km": self.job.get("km", 0)}

        offjob_view = self.offjob and s["mode"] == "auto" and self.connected
        if offjob_view and not (s["offjob_rest"] and status == "OFF_DUTY"):
            tone = "idle"
            label, value = L("big.offjob"), hm(max(remaining, 0)) if remaining >= 0 else "+" + hm(-remaining)
            sub = L("big.offjob_sub")

        tz_src = None
        if self.saves.tz is not None:
            tz_src = f"{self.saves.tz_name or 'tz'} (UTC{hm_signed(self.saves.tz)})"

        return {
            "mode": s["mode"],
            "status": status,
            "conn": conn,
            "job": job,
            "speed": round(self.speed, 1),
            "paused": self.paused,
            "clock": {
                "abs": abs_now,
                "local": local,
                "day": WEEKDAY(local // 1440) if local is not None else "",
                "time": self.clock_text(local),
                "tz_name": self.saves.tz_name if s["mode"] == "auto" else None,
                "offset": off,
            },
            "drive_block": s["drive_block"], "drive_daily": s["drive_daily"], "rest": s["rest"],
            "limits": {"block": DRIVE_BLOCK, "daily": self.daily_limit(), "break": BREAK_MIN, "break2": BREAK_PART2, "rest": REST_MIN},
            "weekly": self.weekly_view(wi, local),
            "wrest": self.wrest_info(),
            "red_rest": self.red_rest_info(),
            "profile": self.profile_view(),
            "plan": self.plan_view(),
            "strict_rest": bool(s["strict_rest"]), "fines": bool(s["fines"]), "voice": bool(s["voice"]),
            "voice_ev": {"seq": self.voice_seq, "text": self.voice_text},
            "rest_target": need if s["rest_credited"] else target,
            "break_part1": s["break_part1"] if (s["split_break"] and s["break_part1"] >= BREAK_PART1) else 0,
            "split_break": bool(s["split_break"]),
            "sounds": bool(s["sounds"]),
            "onboarded": bool(s["onboarded"]),
            "hotkey": s["hotkey"],
            "mini": self.mini_on,
            "win_max": self.win_maxed,
            "mini_opacity": s["mini_opacity"],
            "split": {"part1": s["rest_part1"] if split else 0, "need": need, "done": s["rest_daily_done"],
                      "enabled": bool(s["split_rest"]), "stored": s["rest_part1"] if s["rest_part1"] >= SPLIT_PART1 else 0},
            "remaining": remaining, "next_req": next_req,
            "break_credited": s["break_credited"],
            "big": {"label": label, "value": value, "sub": sub, "tone": tone},
            "seq": seq,
            "seq_boxes": self.sequence(),
            "flags": self.flags(),
            "moving": self.speed > MOVE_KMH and not self.paused and s["mode"] == "auto" and self.connected,
            "offjob": offjob_view,
            "offjob_rest": bool(s["offjob_rest"]),
            "split_locked": bool(s["split_rest"]) and s["rest_part1"] >= SPLIT_PART1,
            "manual_rate": s["manual_rate"],
            "on_top": s["on_top"],
            "lang": L.code,
            "langs": I18n.available(),
            "theme": s["theme"],
            "themes": [{"code": t, "name": L("theme." + t)} for t in THEMES],
            "custom": {"win": s["custom"]["win"], "accent": s["custom"]["accent"], "bg": bool(s["custom"].get("bg")), "bg_rev": self.bg_rev},
            "feedback_url": feedback_url(),
            "yard_max": int(YARD_MAX_KMH),
            "auto_break": s["auto_break"],
            "auto_break_cfg": {"remaining": AUTO_BREAK_REMAINING, "stopped": AUTO_BREAK_STOPPED},
            "skip": self.skip_info(),
            "full_rest": self.full_rest_info(),
            "tz": {"source": tz_src, "adjust": s["tz_adjust"], "offset": off, "error": self.saves.error,
                   "frame": s["set_time_frame"], "base": ETS2_BASE_TZ, "zones_enabled": self.saves.zones_enabled,
                   "console": self.saves.console_ok,
                   "save_age": (self.tel_abs - self.saves.save_time) if (self.tel_abs is not None and self.saves.save_time is not None) else None},
            "log": list(reversed(s["log"][-6:])),
            "tel": {"ok": self.tel_ok, "rev": self.tel_rev, "game": self.game, "abs": self.tel_abs, "scale": round(self.tel_scale, 1)},
        }

    # ---- JS'den çağrılan işlemler ----
    def act_set_status(self, status):
        with self.lock:
            self.act_set_status_locked(status)

    def act_set_status_locked(self, status):
        """act_set_status'ın kilit tutan çağıranlar için sürümü."""
        s = self.s
        cur = s["status"]
        self.auto_yard = None
        if status == "OFF_DUTY":
            if cur == "OFF_DUTY":
                self.no_auto_break = True
                self.set_status("ON_DUTY")
            else:
                self.set_status("OFF_DUTY")
        elif status == "YARD_MOVE":
            self.set_status("ON_DUTY" if cur == "YARD_MOVE" else "YARD_MOVE")
        elif status in STATUSES:
            if cur == "OFF_DUTY":
                self.no_auto_break = True
            self.set_status(status)

    def act_set_mode(self, mode):
        with self.lock:
            if mode not in ("auto", "manual") or mode == self.s["mode"]:
                return
            s = self.s
            if mode == "manual":
                # manuel saat oyunun saatinden devam etsin
                cur = self.current_abs()
                if cur is not None:
                    s["manual_abs"] = float(cur)
            s["mode"] = mode
            s["last_abs"] = None
            s["last_move_abs"] = None
            self.connected = False
            self.log(L("log.mode_manual") if mode == "manual" else L("log.mode_auto"))

    def act_set_manual_clock(self, day, hh, mm):
        with self.lock:
            s = self.s
            week = int(s["manual_abs"]) // 10080
            s["manual_abs"] = float(week * 10080 + int(day) * 1440 + int(hh) * 60 + int(mm))
            s["last_abs"] = int(s["manual_abs"])
            self.log(L("log.clock_set", day=WEEKDAY(day), time=f"{int(hh):02d}:{int(mm):02d}"))

    def act_set_rate(self, rate):
        with self.lock:
            try:
                r = max(0.0, min(600.0, float(rate)))
            except (TypeError, ValueError):
                return
            self.s["manual_rate"] = r
            self.dirty = True

    def act_jump(self, minutes):
        with self.lock:
            m = int(minutes)
            if m <= 0 or self.s["mode"] != "manual":
                return
            self.attribute(m)
            self.s["manual_abs"] += m
            self.s["last_abs"] = int(self.s["manual_abs"])
            self.log(L("log.jumped", d=hm(m)))

    def act_set_on_top(self, flag):
        with self.lock:
            self.s["on_top"] = bool(flag)
            self.dirty = True
        if self.window is not None:
            try:
                set_topmost(self.window, bool(flag))
            except Exception as e:
                log_error("on_top ayarlanamadı: %r" % e)

    def act_set_split_rest(self, flag):
        with self.lock:
            if not flag and self.s["split_rest"] and self.s["rest_part1"] >= SPLIT_PART1:
                self.log(L("log.split_locked"))
                return
            self.s["split_rest"] = bool(flag)
            self.dirty = True
            self.log(L("log.split_on") if flag else L("log.split_off"))

    def act_clear_part1(self):
        with self.lock:
            if self.s["rest_part1"]:
                self.log(L("log.part1_cleared", p1=hm(self.s["rest_part1"])))
                self.s["rest_part1"] = 0
                for seg in reversed(self.s["day_segments"]):
                    if seg["t"] == "rest" and seg.get("k") == "part1":
                        seg["k"] = "break"
                        break
                self.dirty = True

    def act_set_offjob_rest(self, flag):
        with self.lock:
            self.s["offjob_rest"] = bool(flag)
            self.dirty = True
            self.log(L("log.offjob_rest_on") if flag else L("log.offjob_rest_off"))

    def act_set_lang(self, code):
        with self.lock:
            L.load(code)
            self.s["lang"] = L.code
            self.dirty = True
            self.log(L("log.lang", name=L("_name")))

    def act_set_theme(self, theme):
        with self.lock:
            if theme in THEMES:
                self.s["theme"] = theme
                self.dirty = True
                self.log(L("log.theme", name=L("theme." + theme)))
        self._restyle_border()

    def _restyle_border(self):
        if self.window is not None:
            style_frameless_main(self.window, theme_border(self.s))

    # ---- özel tema ----
    def act_set_custom(self, win, accent):
        with self.lock:
            c = self.s["custom"]
            if _hex_ok(win):
                c["win"] = win.lower()
            if _hex_ok(accent):
                c["accent"] = accent.lower()
            self.dirty = True
            is_custom = self.s.get("theme") == "custom"
        if is_custom:
            self._restyle_border()

    def act_set_custom_bg(self, data_url):
        """Sayfadan gelen data:image/...;base64 görselini exe'nin yanına yazar."""
        try:
            head, _, b64 = (data_url or "").partition(",")
            if not head.startswith("data:image/") or not b64:
                return {"ok": False, "err": "bad data"}
            import base64
            raw = base64.b64decode(b64, validate=True)
        except Exception as e:
            return {"ok": False, "err": "decode: %r" % e}
        if len(raw) > CUSTOM_BG_MAX:
            return {"ok": False, "err": "too large"}
        if not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
            return {"ok": False, "err": "not jpeg/png"}
        tmp = CUSTOM_BG_FILE + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, CUSTOM_BG_FILE)
        except Exception as e:
            log_error("özel arka plan yazılamadı: %r" % e)
            return {"ok": False, "err": "write: %r" % e}
        with self.lock:
            self.s["custom"]["bg"] = True
            self.bg_rev += 1
            self.dirty = True
            self.log(L("log.custom_bg"))
        return {"ok": True}

    def act_clear_custom_bg(self):
        try:
            if os.path.exists(CUSTOM_BG_FILE):
                os.remove(CUSTOM_BG_FILE)
        except Exception as e:
            log_error("özel arka plan silinemedi: %r" % e)
        with self.lock:
            self.s["custom"]["bg"] = False
            self.bg_rev += 1
            self.dirty = True

    def custom_bg_data_url(self):
        """Kayıtlı görseli data URL olarak verir (sayfa dosya sisteminden okuyamaz)."""
        if not self.s["custom"].get("bg") or not os.path.exists(CUSTOM_BG_FILE):
            return ""
        try:
            with open(CUSTOM_BG_FILE, "rb") as f:
                raw = f.read()
        except Exception as e:
            log_error("özel arka plan okunamadı: %r" % e)
            return ""
        import base64
        mime = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))

    def act_set_auto_break(self, flag):
        with self.lock:
            self.s["auto_break"] = bool(flag)
            self.dirty = True

    def act_tz_adjust(self, minutes):
        with self.lock:
            self.s["tz_adjust"] = int(minutes)
            self.dirty = True
            self.log(L("log.tz_adjust", d=hm_signed(int(minutes))))

    def act_set_time_frame(self, frame):
        with self.lock:
            if frame in ("base", "local"):
                self.s["set_time_frame"] = frame
                self.dirty = True

    def act_reset(self):
        with self.lock:
            s = self.s
            self._archive_day("reset")
            s["ext_drive"] = s["ext_consumed"] = False
            s["wrest_kind"] = None
            s["last_wrest_end"] = self.local_abs()
            s["drive_block"] = 0
            s["drive_daily"] = 0
            s["rest"] = 0
            s["break_credited"] = False
            s["rest_part1"] = 0
            s["rest_daily_done"] = False
            s["day_segments"] = []
            s["prov"] = None
            s["break_part1"] = 0
            s["rest_credited"] = False
            s["status"] = "ON_DUTY"
            self.hist = []
            self.hist_dirty = True
            self.log(L("log.reset"))


class Api:
    """pywebview'in JS tarafına açtığı köprü."""

    def __init__(self, tacho):
        self._t = tacho

    def get_state(self):
        with self._t.lock:
            return self._t.view()

    def set_status(self, status):
        self._t.act_set_status(status)
        return self.get_state()

    def set_mode(self, mode):
        self._t.act_set_mode(mode)
        return self.get_state()

    def set_manual_clock(self, day, hh, mm):
        self._t.act_set_manual_clock(day, hh, mm)
        return self.get_state()

    def set_rate(self, rate):
        self._t.act_set_rate(rate)
        return self.get_state()

    def jump(self, minutes):
        self._t.act_jump(minutes)
        return self.get_state()

    def set_on_top(self, flag):
        self._t.act_set_on_top(flag)
        return self.get_state()

    def set_auto_break(self, flag):
        self._t.act_set_auto_break(flag)
        return self.get_state()

    def get_strings(self):
        with self._t.lock:
            return L.strings()

    def set_lang(self, code):
        self._t.act_set_lang(code)
        return self.get_state()

    def set_theme(self, theme):
        self._t.act_set_theme(theme)
        return self.get_state()

    def set_custom(self, win, accent):
        self._t.act_set_custom(win, accent)
        return self.get_state()

    def set_custom_bg(self, data_url):
        return self._t.act_set_custom_bg(data_url)

    def clear_custom_bg(self):
        self._t.act_clear_custom_bg()
        return self.get_state()

    def get_custom_bg(self):
        return self._t.custom_bg_data_url()

    def toggle_mini(self):
        self._t.act_toggle_mini()
        return self.get_state()

    def set_hotkey(self, mods, vk, name):
        self._t.act_set_hotkey(mods, vk, name)
        return self.get_state()

    def set_mini_opacity(self, value):
        self._t.act_set_mini_opacity(value)
        return self.get_state()

    def win(self, what):
        self._t.act_win(what)
        return self.get_state()

    def win_resize(self, edge, dx, dy):
        self._t.act_win_resize(edge, dx, dy)
        return True

    def set_sounds(self, flag):
        self._t.act_set_sounds(flag)
        return self.get_state()

    def set_onboarded(self, flag):
        self._t.act_set_onboarded(flag)
        return self.get_state()

    def test_sound(self):
        self._t._sound("done")
        return self.get_state()

    def set_split_break(self, flag):
        self._t.act_set_split_break(flag)
        return self.get_state()

    def open_feedback(self):
        try:
            import webbrowser
            webbrowser.open(feedback_url())
        except Exception as e:
            log_error("geri bildirim bağlantısı açılamadı: %r" % e)
        return self.get_state()

    def set_offjob_rest(self, flag):
        self._t.act_set_offjob_rest(flag)
        return self.get_state()

    def set_split_rest(self, flag):
        self._t.act_set_split_rest(flag)
        return self.get_state()

    def clear_part1(self):
        self._t.act_clear_part1()
        return self.get_state()

    def tz_adjust(self, minutes):
        self._t.act_tz_adjust(minutes)
        return self.get_state()

    def set_time_frame(self, frame):
        self._t.act_set_time_frame(frame)
        return self.get_state()

    def skip_rest(self):
        self._t.act_skip_rest()
        return self.get_state()

    def full_rest(self):
        self._t.act_full_rest()
        return self.get_state()

    def weekly_rest(self):
        self._t.act_weekly_rest()
        return self.get_state()

    def set_strict_rest(self, flag):
        self._t.act_set_flag("strict_rest", flag)
        return self.get_state()

    def set_fines(self, flag):
        self._t.act_set_flag("fines", flag)
        return self.get_state()

    def set_voice(self, flag):
        self._t.act_set_flag("voice", flag)
        return self.get_state()

    def voice_sample(self):
        return L("voice.sample")

    def plan(self, km, hours):
        with self._t.lock:
            try:
                km = float(km) if km not in (None, "") else None
                hours = float(hours) if hours not in (None, "") else None
            except Exception:
                km = hours = None
            return self._t.plan_view(km, hours)

    def set_weekly_rules(self, flag):
        self._t.act_set_weekly_rules(flag)
        return self.get_state()

    def set_auto_ext(self, flag):
        self._t.act_set_auto_ext(flag)
        return self.get_state()

    def set_auto_red(self, flag):
        self._t.act_set_auto_red(flag)
        return self.get_state()

    def red_rest(self):
        self._t.act_red_rest()
        return self.get_state()

    def get_history(self):
        with self._t.lock:
            return self._t.history()

    def reset(self):
        self._t.act_reset()
        return self.get_state()


def rect_on_screen(x, y, w, h):
    """Kayıtlı pencere konumu görünür bir monitörle kesişiyor mu (monitör sökülmüş olabilir)."""
    try:
        u = ctypes.WinDLL("user32")
        r = wt.RECT(int(x), int(y), int(x + w), int(y + h))
        u.MonitorFromRect.restype = wt.HANDLE
        u.MonitorFromRect.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD]
        return bool(u.MonitorFromRect(ctypes.byref(r), 0))  # MONITOR_DEFAULTTONULL
    except Exception:
        return False


def main():
    tacho = Tacho()
    api = Api(tacho)
    kw = {"width": 460, "height": 950}
    win = tacho.s.get("win") or {}
    if all(k in win for k in ("x", "y", "w", "h")) and win["w"] >= 380 and win["h"] >= 600 and rect_on_screen(win["x"], win["y"], win["w"], win["h"]):
        kw = {"x": int(win["x"]), "y": int(win["y"]), "width": int(win["w"]), "height": int(win["h"])}
    theme = tacho.s.get("theme") or DEFAULT_THEME
    window = webview.create_window(
        WINDOW_TITLE,
        UI_FILE + "#" + theme_hash(tacho.s) + f"&lang={L.code}",    # sayfa ilk boyamadan önce temayı (ve özel renkleri) bundan okur
        js_api=api,
        min_size=(380, 600),
        on_top=bool(tacho.s["on_top"]),
        background_color=theme_bg_color(tacho.s),
        frameless=True,            # Windows başlık çubuğu yerine sayfadaki ince bar (temaya uyumlu)
        easy_drag=False,           # pywebview çerçevesizde bunu varsayılan açar: her tıklama pencereyi sürüklerdi;
                                   # taşıma yalnızca başlık çubuğundan (.pywebview-drag-region)
        **kw,
    )

    def shown():
        style_frameless_main(window, theme_border(tacho.s))
        try:
            window.resize(kw["width"], kw["height"])   # çerçeve kaldırılınca 16 px eksilen boyutu geri ver
        except Exception:
            pass
    window.events.shown += shown
    tacho.window = window
    tacho.api = api
    tacho.hotkeys = HotkeyListener(tacho.act_toggle_mini)
    hk = tacho.s.get("hotkey") or {}
    tacho.hotkeys.set(hk.get("mods", 2), hk.get("vk", 96))
    tacho.hotkeys.start()

    def remember(**vals):
        with tacho.lock:
            w = dict(tacho.s.get("win") or {})
            w.update(vals)
            tacho.s["win"] = w
            tacho.dirty = True

    def on_closing():
        try:
            if tacho.win_maxed and tacho.win_normal:
                x, y, w, h = tacho.win_normal
                remember(x=x, y=y, w=w, h=h)
            else:
                remember(x=window.x, y=window.y, w=window.width, h=window.height)
        except Exception as e:
            log_error("pencere konumu okunamadı: %r" % e)
        tacho.stop = True
        tacho.exiting = True
        tacho.save()
        if tacho.hist_dirty:
            tacho._hist_save()
        if tacho.mini_win is not None:
            try:
                tacho.mini_win.destroy()
            except Exception:
                pass

    def on_moved(x, y):
        if not tacho.win_maxed:
            remember(x=x, y=y)

    def on_resized(w, h):
        if not tacho.win_maxed:
            remember(w=w, h=h)

    if hasattr(window.events, "moved"):
        window.events.moved += on_moved
    if hasattr(window.events, "resized"):
        window.events.resized += on_resized
    window.events.closing += on_closing
    threading.Thread(target=tacho.run, daemon=True).start()
    webview.start(icon=ICON_FILE if os.path.exists(ICON_FILE) else None)
    tacho.stop = True
    tacho.save()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error(traceback.format_exc())
        raise
