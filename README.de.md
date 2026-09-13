# OpenTacho

[English](README.md) · [Türkçe](README.tr.md) · **Deutsch** · [Русский](README.ru.md) · [Polski](README.pl.md)

Ein kleiner, konsequenter Lenkzeit-Tachograph für **Euro Truck Simulator 2** und
**American Truck Simulator** — eine Regel, kein Schnickschnack, komplett in **Spielzeit** gemessen:

> **4:30 Fahren → 0:45 Pause → 4:30 Fahren → 11:00 Tagesruhezeit** (oder geteilt **3:00 + 9:00**)

Er liest Spieluhr, Geschwindigkeit, Lkw und Auftragsdaten live aus dem Spiel, zeigt, was dir
noch bleibt, und kümmert sich um die unangenehmen Fälle (Fähren, Schlafen, Zeitzonen, geladene
Spielstände, Anhänger beladen), damit du einfach fahren kannst.

**Nur Windows 10/11** — das Telemetrie-Plugin teilt seine Daten über den gemeinsamen Speicher von Windows, und die App nutzt Windows-APIs für Hotkey, Töne und den Konsolenbefehl. Linux (natives ETS2 / Proton) und macOS werden nicht unterstützt.

<p align="center">
  <img src="docs/screenshots/de/main.jpg" width="300" alt="Hauptfenster">
  <img src="docs/screenshots/de/settings-rules.jpg" width="300" alt="Einstellungen – Reiter Regeln">
</p>
<p align="center">
  <img src="docs/screenshots/de/mini.png" width="540" alt="Mini-Leiste über dem Spiel">
</p>

## Schnellstart

1. **[OpenTacho-win64.zip](https://github.com/onurbalcii/OpenTacho/releases/latest/download/OpenTacho-win64.zip)**
   herunterladen (alle Versionen: [Releases](https://github.com/onurbalcii/OpenTacho/releases)) und irgendwo entpacken.
2. **`plugin\scs-telemetry.dll`** in den Plugin-Ordner des Spiels kopieren
   (`…\Euro Truck Simulator 2\bin\win_x64\plugins\` — `plugins` anlegen, falls nicht vorhanden;
   bei ATS genauso). Das Spiel einmal starten und die SDK-Abfrage bestätigen.
3. **`OpenTacho.exe`** starten. Das war's — alles andere ist dabei. Beim ersten Start wählst du
   deine Sprache und bekommst eine kurze Einführung.

Für die ⏩ Überspringen-Schaltfläche die Entwicklerkonsole im Spiel aktivieren: in
`Documents\Euro Truck Simulator 2\config.cfg` `g_console "1"` und `g_developer "1"` setzen.
Die Schaltfläche bleibt gesperrt, bis beides aktiv ist.

Öffnet sich das Fenster gar nicht, installiere Microsofts kostenlose
[WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) — sie ist in Windows 11 und in jedem
aktuellen Windows 10 bereits enthalten, daher ist das selten nötig.

## Funktionen

- **Live aus dem Spiel** – Uhr, Geschwindigkeit, Lkw-Modell, aktiver Auftrag (Route, Fracht, Zeit bis zur Lieferung).
- **Fahren / Im Dienst aus der Geschwindigkeit erkannt**; **Pause** und **Rangieren** per Knopfdruck.
- **Automatische Pause**, wenn die Fahrzeit fast aufgebraucht ist und der Lkw eine Weile steht.
- **Geteilte Pause (15 + 30)**: ein Halt von ≥ 15 min im 4,5-h-Block wird als Teil 1 behalten;
  eine spätere Pause von ≥ 30 min erneuert den Block (EU-Regel). Abschaltbar.
- **Geteilte Tagesruhezeit (3 + 9)** mit einer Tagesablauf-Leiste, die jeden abgeschlossenen Block,
  den aktuellen und den Plan zeigt; eine einteilige 11-h-Ruhezeit funktioniert weiterhin. Kann auf einteilig festgelegt werden.
- **Zeitsprünge verarbeitet**: Schlafen / Fähre / Zug zählen als Ruhezeit; **Anhänger be- und
  entladen nicht**; das Laden eines Spielstands setzt die Zähler auf diesen Moment zurück.
- **Modus „Kein Auftrag“**: ohne aktive Lieferung zählt nichts (Ruhezeit optional weiterhin). Eine
  GPS-Route startet eine vorläufige Zählung, die rückgängig gemacht wird, wenn du den Auftrag nie annimmst.
- **Rangier-Automatik**: schaltet sich beim Auftragsbeginn und nahe dem Lieferbereich ein, über 40 km/h aus.
- **Lokale Zeitzonen**: das HUD zeigt die Ortszeit des Landes; die App liest die Zone aus dem
  neuesten Spielstand, damit beide Uhren übereinstimmen.
- **⏩ Überspringen**: sendet `g_set_time` an die Spielkonsole, um eine Pause oder Ruhezeit vorzuspulen.
- **Tonwarnungen**: ein kurzer Ton 15 min vor Ende der Fahr-/Ruhezeit, zweimal bei einem Verstoß,
  einmal beim Abschluss einer Pause/Ruhezeit (abschaltbar; `.wav`-Dateien austauschen, um ihn zu ändern).
- **Mini-Leiste**: ein globaler Hotkey (Standard `Strg + Num 0`, änderbar) tauscht das Fenster gegen eine
  kleine, immer sichtbare Anzeige über dem Spiel — Statussymbol, Restzeit, nächster Schritt, drei Mini-Balken,
  Spieluhr, Markierungen, Lieferfrist und Schaltflächen für Pause / Rangieren / Überspringen — frei verschiebbar.
- **Designs**: Van Gogh (Standard, ein algorithmisch gemalter Hintergrund im Stil der *Sternennacht*
  mit Glasflächen), schlicht dunkel, schlicht hell.
- **Sprachen**: Deutsch, Englisch, Türkisch, Russisch, Polnisch — eine neue Sprache ist eine einzige JSON-Datei.
- **Erster Start**: Sprachauswahl, dann eine kurze Einführung in Bildschirm und Einstellungsreiter
  (jederzeit neu startbar unter Einstellungen → App).
- Merkt sich Fensterposition/-größe, Option „immer im Vordergrund“, Ereignisprotokoll, Feedback-Schaltfläche.

## So wird gezählt

| Situation | Was passiert |
|---|---|
| Fahrt > 5 km/h | Fahren. Halte kürzer als ~2 Spielminuten (Ampeln) bleiben Fahren. |
| Stillstand | Im Dienst – Fahrzeit pausiert, Ruhezeit zählt nicht. |
| Schaltfläche **Pause** | Ruhezeit zählt; endet automatisch, sobald der Lkw fährt. |
| **Rangieren** | Bewegung zählt nicht als Fahren; Ruhezeit pausiert, wird nicht verworfen. |
| Zeitsprung (Schlaf, Fähre, Zug) | Nach 4 s Wartezeit als Ruhezeit gezählt. |
| Zeitsprung beim Be-/Entladen | Ignoriert (erkannt über Fracht-geladen-Flag / Auftragsereignisse). |
| Halt von 15–44 min, durch Fahren unterbrochen | Als Pause Teil 1 behalten; eine spätere 30-min-Pause erneuert den 4,5-h-Block. |
| Ruhezeit ≥ 3:00, durch Fahren unterbrochen | Als Teil 1 einer geteilten Ruhezeit behalten; 9:00 später schließt den Tag ab. |
| Spielzeit läuft rückwärts | Spielstand geladen → Zähler aus dem Minutenverlauf wiederhergestellt. |
| Kein aktiver Auftrag und keine GPS-Route | Kein Auftrag: nichts läuft weiter. |

Alles liegt in `state.json` / `history.json` neben `OpenTacho.exe`; lösche sie, um neu zu
beginnen (oder nutze *Zähler zurücksetzen* in den Einstellungen).

## FAQ

**Gibt es einen Tachographen / Lenkzeit-Tracker für Euro Truck Simulator 2 oder American Truck Simulator?**
Ja — genau das ist OpenTacho. Es zählt Lenkzeit, Pausen und Tagesruhezeit in Spielzeit und
liest das Spiel live über das Telemetrie-Plugin scs-sdk-plugin.

**Welche Regel gilt?**
Die EU-Lenkzeitregel: 4:30 Fahren → 45 Minuten Pause → 4:30 Fahren → 11 Stunden Tagesruhezeit,
dazu die geteilte Pause 15 + 30 und die geteilte Tagesruhezeit 3 + 9. Sonst nichts.

**Braucht es Internet oder ein Konto?**
Nein. Alles läuft lokal und offline; nichts wird hochgeladen. Die einzige Verbindung nach außen
ist die optionale Feedback-Schaltfläche, die ein Webformular im Browser öffnet.

**Funktioniert es mit Karten-Mods, ProMods oder ATS?**
Ja. Es liest nur Telemetrie (Uhr, Geschwindigkeit, Auftrag), Karten- und Lkw-Mods spielen
also keine Rolle; American Truck Simulator nutzt dasselbe Plugin.

**Was unterscheidet es von ELD-artigen Lenkzeit-Apps?**
Es ist eine leichte Open-Source-Alternative (MIT): ein Regelsatz, kein Konto, offline, ein
einzelner portabler Ordner mit `.exe`, und es kümmert sich um Fähren, Schlafen, Zeitzonen und
geladene Spielstände.

## Ordnerstruktur

```
OpenTacho.exe          die App (Release-Build)             OpenTacho.py   dieselbe App, aus dem Quellcode
plugin/                scs-telemetry.dll → in den plugins-Ordner des Spiels kopieren
app/                   Fensterinhalt: main.html, mini.html, assets/ (Logo, Hintergrund, Töne)
lang/                  en, tr, de, ru, pl — eine JSON-Datei pro Sprache
lib/                   SII_Decrypt.dll (Zeitzonen-Funktion)
docs/screenshots/      die Bilder dieser READMEs
third_party/           Lizenzen der mitgelieferten Komponenten
tools/                 build.bat (PyInstaller), Asset-Generatoren
```

## Aus dem Quellcode starten / bauen

```powershell
git clone https://github.com/onurbalcii/OpenTacho.git
cd OpenTacho
pip install -r requirements.txt
python OpenTacho.py
```

`pip install pyinstaller` und `tools\build.bat` erzeugen `dist\OpenTacho\OpenTacho.exe` und
`dist\OpenTacho-win64.zip` (das Release-Paket). Benötigt Windows 10/11 mit WebView2 (in Windows enthalten).

## Eine Sprache hinzufügen

Kopiere `lang/en.json` nach `lang/<code>.json`, übersetze die Werte (behalte die `{Platzhalter}`
und die `<b>`/`<code>`-Tags), setze `"_name"`. Die neue Sprache erscheint automatisch unter
Einstellungen → App und in der Sprachauswahl beim ersten Start.

## Komponenten Dritter und Lizenzen

Siehe [`third_party/README.md`](third_party/README.md). OpenTacho steht unter der MIT-Lizenz
([LICENSE](LICENSE)). Es ist ein Fan-Projekt und steht in keiner Verbindung zu SCS Software.
