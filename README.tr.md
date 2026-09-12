# OpenTacho

*[English README](README.md)*

**Euro Truck Simulator 2** ve **American Truck Simulator** için küçük, tek kurallı bir
çalışma-süresi takografı. Her şey **oyun saatiyle** ölçülür:

> **4:30 sürüş → 0:45 mola → 4:30 sürüş → 11:00 günlük dinlenme** (ya da bölünmüş **3:00 + 9:00**)

Oyun saatini, hızı, kamyonu ve aktif işi oyundan canlı okur; kalan sürenizi gösterir ve zor
durumları (feribot, uyku, saat dilimleri, kayıt geri yükleme, dorse yükleme) sizin yerinize
halleder.

![OpenTacho](app/assets/logo_square.png)

**Yalnızca Windows 10/11** — telemetri eklentisi veriyi Windows paylaşımlı belleğiyle aktarır; kısayol, ses ve konsol komutu da Windows API'leri kullanır. Linux (yerel ETS2 / Proton) ve macOS desteklenmez.

## Hızlı başlangıç (Python gerekmez)

1. [Releases](../../releases) sayfasından **`OpenTacho-win64.zip`** dosyasını indir, istediğin yere çıkar.
2. **`plugin\scs-telemetry.dll`** dosyasını oyunun eklenti klasörüne kopyala
   (`…\Euro Truck Simulator 2\bin\win_x64\plugins\` — `plugins` yoksa oluştur; ATS için de aynı).
   Oyunu bir kez başlatıp SDK uyarısını onayla.
3. **`OpenTacho.exe`**'yi çalıştır. Hepsi bu — başka bir şey indirmek gerekmez.

⏩ Atla butonu için oyunda geliştirici konsolunu aç: `Documents\Euro Truck Simulator 2\config.cfg`
içinde `g_console "1"` ve `g_developer "1"`. İkisi de açık olmadıkça buton kilitli kalır.

## Özellikler

- **Oyundan canlı** – saat, hız, kamyon modeli, aktif iş (rota, yük, teslime kalan süre).
- **Sürüş / Görevde** hızdan otomatik; **Mola** ve **İç hareket** tek dokunuş.
- **Otomatik mola**: sürüş hakkı azalıp araç bir süre durunca kendiliğinden.
- **Bölünmüş mola (15 + 30)**: 4,5 saatlik blok içinde ≥ 15 dk duruş 1. parça olarak saklanır;
  sonraki ≥ 30 dk mola bloğu yeniler (AB kuralı). Kapatılabilir.
- **Bölünmüş günlük dinlenme (3 + 9)** ve günün gerçek akışını gösteren kutucuk şeridi
  (tamamlanan bloklar ✓, şu anki, plan). Tek parça 11 saat aynen çalışır; tek parçaya kilitlenebilir.
- **Zaman atlamaları**: uyku / feribot / tren dinlenme sayılır; **dorse yükleme/boşaltma sayılmaz**;
  kayıt yüklenince sayaçlar o ana geri döner.
- **Görev dışı modu**: aktif teslimat yoksa hiçbir şey ilerlemez (dinlenme isteğe bağlı sayılır).
  GPS'e rota koyunca geçici sayım başlar; iş alınmazsa geri alınır.
- **İç hareket otomasyonu**: iş başlayınca ve teslimat sahasına yaklaşınca açılır, 40 km/h üstünde kapanır.
- **Yerel saat dilimleri**: HUD ülkenin yerel saatini gösterir; uygulama dilimi en yeni kayıt
  dosyasından okuyup saati eşitler.
- **⏩ Atla**: oyun konsoluna `g_set_time` göndererek molayı/dinlenmeyi ileri sarar.
- **Sesli uyarı**: sürüş/dinlenme bitimine 15 dk kala kısa bir bildirim sesi, ihlalde iki kez,
  mola/dinlenme tamamlanınca bir kez (kapatılabilir; `.wav` dosyalarını değiştirerek özelleştirilebilir).
- **Mini şerit**: global kısayol (varsayılan `Ctrl + Num 0`, değiştirilebilir) pencereyi oyunun
  üstünde küçük, sürüklenebilir bir göstergeyle değiştirir — durum ikonu, kalan süre, sonraki adım,
  üç mini çubuk, oyun saati, bayraklar, teslimat süresi ve Mola / İç hareket / Atla düğmeleri.
- **Temalar**: Van Gogh (varsayılan; *Yıldızlı Gece* esintili algoritmik arka plan + cam paneller),
  sade koyu, sade açık. **Diller**: Türkçe, İngilizce — yeni dil tek JSON dosyası.
- **İlk açılış**: dil seçimi, ardından ekranı ve ayar sekmelerini adım adım anlatan kısa bir rehber turu
  (Ayarlar → Uygulama'dan her zaman yeniden başlatılabilir).
- Pencere konumu/boyutu hatırlanır, her zaman üstte seçeneği, olay listesi, geri bildirim butonu.

## Nasıl sayar

| Durum | Ne olur |
|---|---|
| 5 km/h üstü hareket | Sürüş. ~2 oyun dakikasından kısa duruşlar (trafik ışığı) sürüşten sayılır. |
| Duruyor | Görevde – sürüş sayacı durur, dinlenme işlemez. |
| **Mola** butonu | Dinlenme işler; araç hareket edince kendiliğinden biter. |
| **İç hareket** | Hareket sürüşe sayılmaz; dinlenme bekler, silinmez. |
| Zaman atlaması (uyku, feribot, tren) | 4 sn bekletildikten sonra dinlenme sayılır. |
| Yükleme/boşaltmadaki atlama | Yok sayılır (yük yüklendi bayrağı / iş olaylarıyla tespit). |
| 15–44 dk duruş sürüşle kesilirse | Mola 1. parçası olarak saklanır; sonraki 30 dk mola 4,5 saatlik bloğu yeniler. |
| ≥ 3:00 dinlenme sürüşle kesilirse | Bölünmüş dinlenmenin 1. kısmı olarak saklanır; sonraki 9:00 günü tamamlar. |
| Oyun saati geri giderse | Kayıt yüklendi → sayaçlar dakikalık geçmişten o ana döner. |
| Aktif iş ve GPS rotası yoksa | Görev dışı: hiçbir şey ilerlemez. |

Her şey `OpenTacho.exe`'nin yanındaki `state.json` / `history.json` dosyalarında tutulur; sıfırdan
başlamak için silebilirsin (ya da Ayarlar → *Sayaçları sıfırla*).

## Klasör düzeni

```
OpenTacho.exe          uygulama (sürüm paketi)             OpenTacho.py   aynı uygulama, kaynaktan
plugin/                scs-telemetry.dll → oyunun plugins klasörüne kopyalanır
app/                   pencere içeriği: main.html, mini.html, assets/ (logo, arka plan, sesler)
lang/                  tr.json, en.json — dil başına bir dosya
lib/                   SII_Decrypt.dll (saat dilimi özelliği)
third_party/           paketlenen bileşenlerin lisansları
tools/                 build.bat (PyInstaller), görsel/ses üreticiler
```

## Kaynaktan çalıştırma / derleme

```powershell
git clone https://github.com/onurbalcii/OpenTacho.git
cd OpenTacho
pip install -r requirements.txt
python OpenTacho.py
```

`pip install pyinstaller` sonrası `tools\build.bat`, `dist\OpenTacho\OpenTacho.exe` ve
`dist\OpenTacho-win64.zip` (sürüm paketi) üretir. Windows 10/11 ve WebView2 gerekir (Windows ile gelir).

## Geri bildirim

Ayarlar → Uygulama → **Geri bildirim gönder**, uygulamanın dilindeki formu açar. Kendi çatalını
yayınlamadan önce `OpenTacho.py` başındaki `FEEDBACK_URLS` bağlantılarını kendi formlarınla değiştir.

## Yeni dil ekleme

`lang/en.json` dosyasını `lang/<kod>.json` olarak kopyala, değerleri çevir (`{yer_tutucuları}`
koru), `"_name"` alanını yaz. Yeni dil Ayarlar → Uygulama'da kendiliğinden görünür.

## Üçüncü taraf bileşenler ve lisanslar

Bkz. [`third_party/README.md`](third_party/README.md). OpenTacho MIT lisanslıdır ([LICENSE](LICENSE)).
Hayran yapımı bir araçtır; SCS Software ile bağlantısı yoktur.
