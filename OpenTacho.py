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
JOB_EVENT_WINDOW = 15  # sn: iş olayı (başlangıç/bitiş) ile zaman atlaması bu kadar yakınsa "yükleme" sayılır
JUMP_HOLD_S = 4        # sn: zaman atlaması karara bağlanmadan önce iş olayı beklenir
HIST_MINUTES = 12 * 60 # kayıt geri yükleme için tutulan sayaç geçmişi (oyun dk)
ROLLBACK_MIN = 2       # oyun saati en az bu kadar dk geri giderse "kayıt yüklendi" sayılır
TICK = 0.25            # saniye

STATUSES = ("DRIVING", "ON_DUTY", "OFF_DUTY", "YARD_MOVE")
THEMES = ("vangogh", "dark", "light")


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
    "day_segments": [],        # günün akışı: [{"t": "drive"|"rest", "m": dk, "k": rest türü}] (akış kutucukları için)
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
    "theme": DEFAULT_THEME,    # vangogh | dark | light
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
    OFF_JOB_START = 444    # gameplay_ui.jobStartingTime (oyun dk)
    OFF_CARGO_LOADED = 1564  # truck_b.isCargoLoaded (5. bölge): yükleme/boşaltma anı
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


def style_frameless_main(win, theme):
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
        col = ctypes.c_uint32(THEME_BORDER.get(theme, THEME_BORDER["vangogh"]))
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

    # ---- kalıcılık ----
    def _load(self):
        st = dict(DEFAULT_STATE)
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
        return min(DRIVE_BLOCK - s["drive_block"], DRIVE_DAILY - s["drive_daily"])

    def split_active(self):
        """Bölünmüş dinlenme açık ve geçerli bir 1. kısım saklı mı."""
        return bool(self.s["split_rest"]) and self.s["rest_part1"] >= SPLIT_PART1

    def daily_need(self):
        """Günlük dinlenmeyi tamamlamak için gereken kesintisiz süre: 11 sa, ya da 1. kısım varsa 9 sa."""
        return SPLIT_PART2 if self.split_active() else REST_MIN

    def break_need(self):
        """Bu blok için gereken mola: 1. parça (>= 15 dk) alındıysa 30, yoksa 45."""
        return BREAK_PART2 if (self.s["split_break"] and self.s["break_part1"] >= BREAK_PART1) else BREAK_MIN

    def rest_target(self):
        """Şu an işe yarayan dinlenme hedefi (mola ihtiyacı ya da günlük ihtiyaç)."""
        s = self.s
        rem_block = DRIVE_BLOCK - s["drive_block"]
        rem_daily = DRIVE_DAILY - s["drive_daily"]
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
            segs.append({"t": t, "m": d})

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
            self._sound("done")
        if not s["rest_daily_done"] and after >= need:
            s["drive_block"] = 0
            s["drive_daily"] = 0
            s["break_credited"] = False
            s["rest_daily_done"] = True
            if self.split_active():
                self.log(L("log.split_done", p1=hm(s["rest_part1"]), p2=hm(need)))
            else:
                self.log(L("log.daily_done"))
            s["rest_part1"] = 0
            s["break_part1"] = 0
            s["day_segments"] = []   # yeni gün: akış sıfırdan
            self._sound("done")
        self.dirty = True

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
            s["drive_block"] += d
            s["drive_daily"] += d
            r = s["rest"]
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
            self._seg_add("drive", d)
        elif st == "OFF_DUTY":
            self.add_rest(d)
        # ON_DUTY / YARD_MOVE: sürüş sayılmaz, dinlenme de ilerlemez (ama sıfırlanmaz)
        self.dirty = True

    # ---- tik ----
    PROV_KEYS = ("status", "drive_block", "drive_daily", "rest", "break_credited", "rest_part1", "rest_daily_done", "day_segments",
                 "break_part1", "rest_credited")

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
            self.s[k] = copy.deepcopy(sn[k])
        self.auto_yard = None
        self.pending_jump = None
        self.hist = [(a, x) for (a, x) in self.hist if a <= now]
        self.hist_dirty = True
        self.dirty = True
        self.log(L("log.rollback", back=hm(back), at=self.clock_text(abs_t + self.tz_offset())))

    def _restore(self, snap):
        import copy
        for k in self.PROV_KEYS:
            self.s[k] = copy.deepcopy(snap[k])
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
        near_event = self.job_event_at is not None and abs(self.job_event_at - pj["t"]) <= JOB_EVENT_WINDOW
        if near_event:
            self.log(L("log.jump_loading", d=hm(d)))
            self.pending_jump = None
            return
        if time.monotonic() - pj["t"] < JUMP_HOLD_S:
            return
        if self.offjob and not s["offjob_rest"]:
            self.log(L("log.jump_offjob", d=hm(d)))
        else:
            self.log(L("log.jump_rest", d=hm(d)))
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
        self.connected = self.tel_ok and tel["time_abs"] > 0
        if not self.connected:
            return
        self.tel_abs = now = tel["time_abs"]
        s = self.s
        job_on = bool(self.job and self.job.get("on"))
        job_start = self.job.get("start") if self.job else None
        loaded = bool(self.job and self.job.get("loaded"))
        if not was or s["last_abs"] is None:
            # yeni bağlantı: saati eşitle, aradaki süreyi sayma
            s["last_abs"] = now
            s["last_move_abs"] = now
            self.prev_job_on, self.prev_job_start, self.prev_loaded = job_on, job_start, loaded
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
        self.prev_job_on, self.prev_job_start, self.prev_loaded = job_on, job_start, loaded

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
        blocked = None
        if self.paused:
            blocked = L("skip.blocked_paused")
        elif self.saves.console_ok is False:
            blocked = L("skip.blocked_console")
        elif s["set_time_frame"] == "local" and self.saves.zones_enabled and self.game != 2:
            # komut HUD saatini aldığı için dilim farkı güncel olmalı; yoksa 23 saat ileri fırlayabilir
            if self.saves.tz is None:
                blocked = L("skip.blocked_tz")
            elif self.saves.save_time is not None and self.tel_abs - self.saves.save_time > SAVE_FRESH_MIN:
                blocked = L("skip.blocked_stale")
        return {
            "available": True,
            "busy": self.skip is not None,
            "blocked": blocked,
            "minutes": need + SKIP_MARGIN,
            "label": L("skip.break") if target in (BREAK_MIN, BREAK_PART2) else (L("skip.part2") if self.split_active() else L("skip.daily")),
        }

    def act_skip_rest(self):
        with self.lock:
            info = self.skip_info()
            if not info["available"] or info["busy"] or info["blocked"]:
                return
            need = info["minutes"]
            base_now = self.tel_abs
            frame_now = base_now if self.s["set_time_frame"] == "base" else base_now + self.tz_offset()
            target = frame_now + need
            cmd = f"g_set_time {(target % 1440) // 60} {target % 60}"
            self.skip = {"cmd": cmd, "need": need, "start_abs": base_now, "t0": time.monotonic()}
            self.log(L("log.skip_sending", d=hm(need), cmd=cmd))
        threading.Thread(target=self._skip_worker, daemon=True).start()

    def _skip_worker(self):
        sk = self.skip
        try:
            ok, err = self.console.send_command(sk["cmd"])
            if not ok:
                with self.lock:
                    self.log(L("log.skip_failed", err=err))
                return
            # oyun saati değişsin diye bekle (duraklatılmışsa devam edince görünür)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 8 and self.tel_abs == sk["start_abs"]:
                time.sleep(0.2)
            time.sleep(0.5)
            delta = (self.tel_abs or sk["start_abs"]) - sk["start_abs"]
            with self.lock:
                if delta == 0:
                    self.log(L("log.skip_nochange"))
                elif abs(delta - sk["need"]) <= 2:
                    self.log(L("log.skip_ok", d=hm(delta)))
                else:
                    self.log(L("log.skip_mismatch", need=hm(sk["need"]), d=hm(delta)))
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
        rem_daily = DRIVE_DAILY - s["drive_daily"]
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
            limit = max(0, min(DRIVE_BLOCK, DRIVE_DAILY - before_block))
            boxes.append({"k": "drive", "v": hm(max(0, limit - block_used_before)), "st": "active"})
            after = DRIVE_DAILY - (before_block + limit)
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
            elif prev["rem"] > 0 >= rem:
                self._sound("alert")
                self.log(L("log.snd_drive0"))
        prev["rem"] = rem if active else None
        if s["status"] == "OFF_DUTY":
            target = self.daily_need() if s["rest_credited"] else self.rest_target()
            left = target - s["rest"]
            if prev["rest_left"] is not None and prev["rest_left"] > ALERT_BEFORE >= left > 0:
                self._sound("warn")
                self.log(L("log.snd_rest15", d=hm(left)))
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
            win = webview.create_window(WINDOW_TITLE + " Mini", OVERLAY_FILE + "#theme=" + theme, js_api=self.api,
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
        return f

    # ---- görünüm ----
    def view(self):
        s = self.s
        abs_now = self.current_abs()
        off = self.tz_offset()
        local = None if abs_now is None else abs_now + off
        rem_block = DRIVE_BLOCK - s["drive_block"]
        rem_daily = DRIVE_DAILY - s["drive_daily"]
        remaining = min(rem_block, rem_daily)
        next_req = "REST" if rem_daily <= rem_block else "BREAK"
        need_txt = L("need.part2", d=hm(self.daily_need())) if self.split_active() else L("need.daily")
        next_txt = need_txt if next_req == "REST" else L("need.break")
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
            "limits": {"block": DRIVE_BLOCK, "daily": DRIVE_DAILY, "break": BREAK_MIN, "break2": BREAK_PART2, "rest": REST_MIN},
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
            "feedback_url": feedback_url(),
            "yard_max": int(YARD_MAX_KMH),
            "auto_break": s["auto_break"],
            "auto_break_cfg": {"remaining": AUTO_BREAK_REMAINING, "stopped": AUTO_BREAK_STOPPED},
            "skip": self.skip_info(),
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
        UI_FILE + f"#theme={theme}&lang={L.code}",    # sayfa ilk boyamadan önce temayı bundan okur
        js_api=api,
        min_size=(380, 600),
        on_top=bool(tacho.s["on_top"]),
        background_color=THEME_BG.get(theme, "#0d1117"),
        frameless=True,            # Windows başlık çubuğu yerine sayfadaki ince bar (temaya uyumlu)
        easy_drag=False,           # pywebview çerçevesizde bunu varsayılan açar: her tıklama pencereyi sürüklerdi;
                                   # taşıma yalnızca başlık çubuğundan (.pywebview-drag-region)
        **kw,
    )

    def shown():
        style_frameless_main(window, tacho.s.get("theme") or DEFAULT_THEME)
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
