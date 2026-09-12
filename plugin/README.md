# Game plugin / Oyun eklentisi

**EN** — Copy `scs-telemetry.dll` into the game's plugin folder, then start the game once and
accept the SDK prompt:

```
…\Euro Truck Simulator 2\bin\win_x64\plugins\scs-telemetry.dll
…\American Truck Simulator\bin\win_x64\plugins\scs-telemetry.dll
```

Create the `plugins` folder if it does not exist. (Steam: right-click the game → *Manage* →
*Browse local files* to find the game folder.)

**TR** — `scs-telemetry.dll` dosyasını oyunun eklenti klasörüne kopyala, oyunu bir kez başlatıp
SDK uyarısını onayla:

```
…\Euro Truck Simulator 2\bin\win_x64\plugins\scs-telemetry.dll
…\American Truck Simulator\bin\win_x64\plugins\scs-telemetry.dll
```

`plugins` klasörü yoksa oluştur. (Steam: oyuna sağ tık → *Yönet* → *Yerel dosyalara göz at*.)

---

`scs-telemetry.dll` is the 64-bit build of [scs-sdk-plugin](https://github.com/RenCloud/scs-sdk-plugin)
v1.12.1 by RenCloud, MIT licensed — see `../third_party/LICENSE-scs-sdk-plugin.txt`.
