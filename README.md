# ezd2lbrn

**EZCAD2-Projektdateien (`.ezd`) nach LightBurn (`.lbrn2`) umwandeln – komplett offline.**

Der wichtigste Unterschied zu Online-Konvertern: **Texte bleiben in LightBurn
bearbeitbare Textobjekte** und werden nicht zu Kurven. Schrift, Fettschrift,
Höhe, Position, Ausrichtung sowie Zeilen- und Zeichenabstand werden aus der
EZD-Datei übernommen.

Entstanden beim Umstieg von einem Faserlaser mit EZCAD2 (Laserquelle
LG-20-J-01) auf eine Haas HL 50E mit LightBurn. Typischer Anwendungsfall sind
Typenschilder (Eloxal, Fill-Gravur).

- Version: **1.8**
- Lizenz: **MIT** – Copyright (c) 2026 Christopher Schmick, [iot-simply.com](https://iot-simply.com)
- Keine Internetverbindung nötig, es werden keine Daten übertragen.

---

## Inhalt

- [Funktionen](#funktionen)
- [Installation](#installation)
- [Bedienung: Oberfläche](#bedienung-oberfläche)
- [Bedienung: Kommandozeile](#bedienung-kommandozeile)
- [Größe und Lage](#größe-und-lage)
- [Vorlagen](#vorlagen)
- [Schriften ersetzen](#schriften-ersetzen)
- [Vorschau](#vorschau)
- [Einschränkungen](#einschränkungen)
- [SmartScreen-Hinweis](#smartscreen-hinweis)
- [Haftung und Sicherheit](#haftung-und-sicherheit)
- [Marken](#marken)
- [Stand der Prüfung](#stand-der-prüfung)
- [Mögliche nächste Schritte](#mögliche-nächste-schritte)
- [Technische Dokumentation](#technische-dokumentation)
- [Lizenz](#lizenz)
- [English summary](#english-summary)

---

## Funktionen

Was übernommen wird:

- **Texte** als echte, bearbeitbare LightBurn-Textobjekte: Schriftart, Fett,
  Höhe, Position, Ausrichtung, Zeilenabstand und die EZCAD-Zeichenbreite
  (als horizontale Skalierung im Text-Transform, wie EZCAD bzw. Windows rechnet).
- **Schraffuren** werden zu Fill-Layern (`Scan`) mit Linienabstand, Winkel und
  Kreuzschraffur.
- **Kurven, Rechtecke, Kreise, Ellipsen, Polygone, Spiralen** sowie importierte
  DXF-/SVG-Vektordaten werden zu Pfaden (Linien und kubische Beziers).
- **Stifte werden zu Layern**: Leistung, Geschwindigkeit, Frequenz, Pulsbreite,
  Ein-/Aus-Verzögerung, Sprunggeschwindigkeit und Durchgänge – sofern keine
  Vorlage gewählt ist.
- **Vorlage** (optional): Geräte- und Laserparameter aus einer vorhandenen
  `.lbrn2` übernehmen.
- **Stapelverarbeitung**: mehrere `.ezd` in einem Durchgang.
- **Vorschau** mit Quelle und Ziel überlagert, Zoom und PNG-Export.
- Grafische Oberfläche **und** Kommandozeile.

Nicht unterstützte Objekte werden im Log und in den LightBurn-Notizen vermerkt,
siehe [Einschränkungen](#einschränkungen).

---

## Installation

### Voraussetzungen

- **Python 3.9 oder neuer** ([python.org](https://www.python.org/downloads/),
  bei der Installation den Haken bei *Add Python to PATH* setzen)
- `tkinter` – gehört zum Python-Standardumfang unter Windows und macOS;
  unter Linux ggf. nachinstallieren (z. B. `sudo apt install python3-tk`)
- `fonttools` – für exakte Schriftmetriken, **optional** (ohne fontTools werden
  Arial-Werte angenommen)
- `pillow` – für die Logo-Skalierung und den PNG-Export der Vorschau, **optional**

### Variante A: mit Python starten

```bash
pip install -r requirements.txt
python ezd2lbrn.py
```

Unter Windows genügt ein Doppelklick auf **`start_ezd2lbrn.bat`**. Das Skript
installiert fehlende Pakete selbst und startet die Oberfläche.

### Variante B: fertige `.exe` herunterladen

Unter [Releases](https://github.com/iot-simply/ezd2lbrn/releases) liegt eine
fertig gebaute `ezd2lbrn.exe`. Sie wird von GitHub Actions auf einem
Windows-Runner aus genau dem Quelltext dieses Repositorys gebaut, der zum
jeweiligen Release gehört. Sie läuft ohne installiertes Python.

Der Release-Text nennt Größe und **SHA-256** der Datei. Nach dem Herunterladen
prüfen (PowerShell im Download-Ordner):

```powershell
Get-FileHash .\ezd2lbrn.exe -Algorithm SHA256
```

Stimmt der Wert nicht überein, war der Download unvollständig. Meldet Windows
beim Start `Could not load PyInstaller's embedded PKG archive`, ist genau das
passiert: Datei löschen und neu herunterladen.

### Variante C: eigenständige `.exe` selbst bauen

**`build_exe.bat`** einmal ausführen. Es installiert `fonttools`, `pillow` und
`pyinstaller`, exportiert das eingebettete Programmsymbol und baut mit
PyInstaller (`--onefile --noconsole`) die Datei **`dist\ezd2lbrn.exe`**.
Diese läuft ohne installiertes Python.

Von Hand entspricht das:

```bash
pip install -r requirements-build.txt
python ezd2lbrn.py --icon-export ezd2lbrn_icon.png
python -m PyInstaller --onefile --noconsole --name ezd2lbrn --icon ezd2lbrn_icon.png ^
    --collect-submodules fontTools --hidden-import PIL.ImageDraw --hidden-import PIL.ImageTk ezd2lbrn.py
```

> Hinweis: Die so gebaute `.exe` ist nicht signiert, Windows zeigt beim ersten
> Start eine SmartScreen-Warnung – siehe [SmartScreen-Hinweis](#smartscreen-hinweis).

---

## Bedienung: Oberfläche

Start ohne Argumente (`python ezd2lbrn.py`, `start_ezd2lbrn.bat` oder
`ezd2lbrn.exe`).

1. **„EZD-Dateien …“** – eine oder mehrere `.ezd` auswählen (Stapelverarbeitung).
2. **Zielordner** wählen. Standard ist der Ordner der EZD-Datei.
3. **Galvo-Feld (mm)** – die Feldgröße des LightBurn-Geräts. Die EZCAD-Mitte
   (0/0) wird auf die Feldmitte gelegt. Ragt das Design über das Feld hinaus,
   erscheint eine Warnung im Log.
4. **Größe** einstellen – siehe [Größe und Lage](#größe-und-lage).
5. **Vorlage** (optional) – siehe [Vorlagen](#vorlagen).
6. **Schriften ersetzen** (optional) – siehe [Schriften ersetzen](#schriften-ersetzen).
7. **„Vorschau …“** (optional) – siehe [Vorschau](#vorschau).
8. **„Konvertieren“**.

Unten rechts führt **„Lizenzen“** zu allen Lizenztexten – dort lassen sie sich
über einen Abschnittswähler durchblättern und als Datei speichern. **„Info“**
zeigt Fassung, Zweck, Gefahrenhinweis, Copyright und Marken und enthält den
Schalter für das **dunkle Design**; die Wahl wird mit den übrigen Einstellungen
gespeichert.

Alle Eingaben (Ordner, Vorlage, Feldgröße, Optionen, Schriftzuordnungen) werden
gespeichert und beim nächsten Start wieder geladen:

```
%APPDATA%\ezd2lbrn\einstellungen.json
```

Unten rechts öffnet **„Lizenzen“** die vollständigen Lizenztexte aller
verwendeten Fremdkomponenten.

---

## Bedienung: Kommandozeile

```bash
python ezd2lbrn.py datei.ezd [weitere.ezd ...] [Optionen]
```

Wird mindestens eine Datei angegeben, läuft das Programm ohne Oberfläche.

| Option | Bedeutung |
|---|---|
| `eingabe` | Eine oder mehrere `.ezd`-Dateien (Pflichtangabe). |
| `-o`, `--ausgabe AUSGABE` | Ausgabedatei (bei genau einer Eingabe) oder Zielordner. Ohne Angabe wird neben die Quelldatei geschrieben. |
| `--feld FELD` | Galvo-Feldgröße in mm. Standard `110`. |
| `--einpassen` | Design gleichmäßig verkleinern, bis es ins Feld passt (Skalierung um die EZCAD-Mitte). |
| `--rand RAND` | Rand zum Feldrand beim Einpassen in mm. Standard `1`. |
| `--skalierung SKALIERUNG` | Feste Skalierung in Prozent, ohne `--einpassen`. Standard `100`. |
| `--vorlage VORLAGE` | `.lbrn2`-Vorlage für Gerät und Layer-Parameter (Layer-Nummer = EZCAD-Stift). |
| `--vorlage-groesse` | Größe und Lage des Vorlagen-Inhalts übernehmen (proportional eingepasst, mittig). Nur zusammen mit `--vorlage`. |
| `--vorlage-rand VORLAGE_RAND` | Abstand zum Vorlagen-Kasten in mm bei `--vorlage-groesse`. Standard `0`. |
| `--ohne-zeichenabstand` | Zeichenabstand nicht an EZCAD angleichen. |
| `--schrift ALT=NEU` | Schrift ersetzen, z. B. `--schrift Romans=Arial`. Mehrfach angebbar. |
| `-h`, `--help` | Hilfe anzeigen. |

Zusätzliche Spezialaufrufe:

| Aufruf | Bedeutung |
|---|---|
| `python ezd2lbrn.py --lizenzen` | Gibt alle eingebetteten Lizenztexte aus (auch `--licenses`). |
| `python ezd2lbrn.py --selbsttest` | Prüft das Programm selbst: baut die Fenster auf, wandelt einen kleinen Entwurf um und prüft die Lizenztexte. Rückgabewert 0 = bestanden. Der Bau-Workflow führt ihn gegen die fertige `.exe` aus. |
| `python ezd2lbrn.py --icon-export datei.png` | Schreibt das eingebettete Programmsymbol als PNG (wird von `build_exe.bat` genutzt). |

### Beispiele

```bash
# Eine Datei, Standardfeld 110 mm, Originalgröße
python ezd2lbrn.py typenschild.ezd

# Mehrere Dateien in einen Zielordner, Feld 150 mm
python ezd2lbrn.py *.ezd -o ausgabe --feld 150

# Auf das Feld einpassen, 2 mm Rand
python ezd2lbrn.py schild.ezd --feld 110 --einpassen --rand 2

# Laserparameter aus einer Vorlage, Größe und Lage ebenfalls aus der Vorlage
python ezd2lbrn.py schild.ezd --vorlage hl50e.lbrn2 --vorlage-groesse --vorlage-rand 2

# EZCAD-Schrift durch eine installierte Schrift ersetzen
python ezd2lbrn.py schild.ezd --schrift Romans=Arial --schrift JSF=Arial
```

Pro Datei wird eine Zeile `OK …` mit der Anzahl der Texte und Pfade sowie der
verwendeten Skalierung ausgegeben, Hinweise zu nicht unterstützten Objekten
darunter. Der Rückgabewert ist `1`, wenn mindestens eine Datei fehlschlug.

---

## Größe und Lage

Es gibt drei sich ausschließende Modi:

1. **Feste Skalierung (%)** – `100` ist Originalgröße. Standard.
2. **Auf Feld einpassen** (`--einpassen`) – verkleinert das gesamte Design
   gleichmäßig, bis es mit dem eingestellten Rand ins Feld passt. Skaliert wird
   um die EZCAD-Mitte, die Lage zur Feldmitte (Aufspannung) bleibt also
   erhalten. **Vergrößert wird nie.**
3. **Größe und Lage aus Vorlage** (`--vorlage-groesse`) – das Design wird
   proportional so skaliert, dass es in den Inhaltsrahmen der Vorlage passt, und
   mittig darauf gelegt. „Auf Feld einpassen“ und „Skalierung“ sind dann
   abgeschaltet.

Mitskaliert werden Texthöhen, Zeilen- und Zeichenabstände, Positionen und
Konturen. **Der Schraffur-Linienabstand bleibt unverändert**, da er ein
physikalischer Laserparameter ist.

---

## Vorlagen

Eine Vorlage ist eine `.lbrn2`-Datei mit den eigenen Laser-Einstellungen.
Übernommen werden Gerätedaten (Root-Attribute, `UIPrefs`, `GalvoRotaryConfig`,
`VariableText`) und die Laserparameter je Layer-Index.

- **Zuordnung:** EZCAD-Stift N → Vorlagen-Layer mit Index N (`C00` = Stift 0).
- Linienabstand, Winkel und Kreuzschraffur kommen **immer** aus der EZD-Datei,
  nie aus der Vorlage.
- Weitere Varianten desselben Stifts (z. B. ein anderer Schraffurwinkel)
  bekommen freie Layer-Indizes. Maximal 30 Layer.
- **Die Vorlage muss einmal in LightBurn gespeichert worden sein**, damit Texte
  ihre Kurvenkopie (BackupPath) enthalten – daraus wird der Inhaltsrahmen
  gemessen.

Mit **„Größe und Lage aus Vorlage übernehmen“** wird das Design zusätzlich in
den Inhaltsrahmen der Vorlage eingepasst; **„Rand zum Kasten (mm)“** legt den
Abstand fest, den das Design rundum zum Vorlagen-Kasten hält.

---

## Schriften ersetzen

Nach Auswahl der EZD-Datei zeigt das Log alle verwendeten Schriften und ob sie
im System installiert sind. In der Oberfläche werden Quelle (aus der EZD) und
Ziel (installierte Schriften) gewählt und mit **„Hinzufügen“** zugeordnet; die
Zuordnungen bleiben gespeichert. Auf der Kommandozeile entspricht das
`--schrift ALT=NEU`.

Das ist vor allem für EZCAD-eigene JSF-/SHX-Schriften nötig, die es unter
Windows nicht als TrueType gibt. Ist eine Schrift nicht installiert, zeigt
LightBurn beim Öffnen eine Warnung.

---

## Vorschau

**„Vorschau …“** zeigt Quelle (EZD, schwarz) und Ziel (rot) überlagert oder
einzeln, dazu das Galvo-Feld und den Vorlagen-Kasten.

- Mausrad = Zoom, linke Maustaste = verschieben, **„Gesamtansicht“** = alles
  wieder ins Fenster
- Schraffur der EZD zuschaltbar
- **„Aktualisieren“** übernimmt geänderte Einstellungen
- **„Als PNG“** exportiert das Bild (benötigt Pillow)

Gespeichert wird nur über **„Konvertieren“** im Hauptfenster.

> **Wichtig:** Die Zielansicht ist eine Nachbildung des LightBurn-Textsatzes mit
> der installierten Schrift. Maßgeblich ist die Darstellung in LightBurn selbst.

---

## Einschränkungen

Nicht unterstützt bzw. vereinfacht (Hinweis im Log und in den LightBurn-Notizen):

- Bitmaps und Barcodes
- EZCAD-Arrays
- Steuerobjekte (Timer, IO, Achsen)
- Variabler Text – wird als fester Text übernommen
- Ausgeblendete Objekte
- Abgerundete Rechteckecken, Ellipsenbögen
- Kursive Schrift
- EZCAD-eigene JSF-/SHX-Schriften – über [Schrift ersetzen](#schriften-ersetzen) lösbar

Getestet ist ausschließlich **EZCAD2**, nicht EZCAD3.

**Laserparameter vor dem ersten Lauf immer an Testmaterial prüfen.**

---

## SmartScreen-Hinweis

Die selbst gebaute `ezd2lbrn.exe` ist nicht code-signiert. Windows zeigt daher
beim ersten Start „Der Computer wurde durch Windows geschützt“:

1. Auf **„Weitere Informationen“** klicken.
2. Dann auf **„Trotzdem ausführen“**.

Beim Download über den Browser kann zusätzlich ein Hinweis erscheinen, dass die
Datei „nicht häufig heruntergeladen wird“ – dort über **„Beibehalten“** bzw.
**„Zulassen“** bestätigen. Alternativ in den Dateieigenschaften der `.exe` den
Haken bei **„Zulassen“** (Blockierung aufheben) setzen.

Wer das vermeiden will, baut die `.exe` mit `build_exe.bat` einfach selbst aus
dem Quelltext.

---

## Haftung und Sicherheit

ezd2lbrn wird **ohne jede Gewährleistung** weitergegeben (siehe [LICENSE](LICENSE)).

Das Programm steuert keinen Laser, es wandelt nur Dateien um. Die erzeugte
`.lbrn2` enthält jedoch Laserparameter (Leistung, Geschwindigkeit, Frequenz,
Pulsbreite), die aus der EZD-Datei oder aus einer Vorlage stammen.

> **Vor dem ersten Lauf immer an Testmaterial prüfen.** Geometrie, Größe,
> Position und sämtliche Laserparameter in LightBurn kontrollieren. Falsche
> Parameter können Werkstück, Optik oder Laserquelle beschädigen. Die üblichen
> Sicherheitsregeln für den Betrieb eines Lasers gelten unverändert.

Die Umsetzung der Textdarstellung ist eine Nachbildung des LightBurn-Satzes.
Maßgeblich ist immer, was LightBurn selbst anzeigt – siehe
[Stand der Prüfung](#stand-der-prüfung).

---

## Marken

ezd2lbrn ist ein **unabhängiges** Programm. Es besteht keine Verbindung zu den
Herstellern der genannten Programme, Steuerungen und Geräte; ezd2lbrn wird von
ihnen weder herausgegeben noch unterstützt, geprüft oder freigegeben.

Die Namen werden ausschließlich genannt, um zu beschreiben, mit welchen
Dateien, Schriften und Geräten das Programm umgehen kann:

| Name | Inhaber |
|---|---|
| EZCAD, JCZ | Beijing JCZ Technology Co., Ltd. |
| LightBurn | LightBurn Software, LLC |
| Haas | Haas Automation, Inc. |
| AutoCAD, DXF | Autodesk, Inc. |
| Windows, Microsoft, Segoe UI | Microsoft Corporation |
| Arial | Monotype Imaging Inc. |
| Python | Python Software Foundation |
| Tcl, Tk | Tcl Core Team |

Weitere genannte Namen können Marken ihrer jeweiligen Inhaber sein, auch wenn
sie hier nicht eigens aufgeführt sind. Aus dem Fehlen eines Hinweises lässt
sich nicht schließen, dass ein Name frei verwendbar wäre.

Es werden **keine** Programmteile, Bibliotheken, Schriften oder Dateien dieser
Hersteller mitgeliefert.

Das Lesen des EZD-Formats dient der Herstellung von Interoperabilität zwischen
eigenständigen Programmen (§ 69e UrhG, Art. 6 der EU-Richtlinie 2009/24/EG).

### Ihre Dateien

An Ihren EZD-Projekten, den daraus entstehenden LightBurn-Dateien und Ihren
Vorlagen erwirbt niemand Rechte – sie bleiben vollständig Ihr Eigentum. Das
Programm arbeitet ausschließlich auf Ihrem Rechner, überträgt nichts ins
Internet und sendet keine Nutzungsdaten.

---

## Stand der Prüfung

- Getestet mit einer echten EZD-Datei (Typenschild 118 × 80 mm, Arial fett und
  normal, mehrzeilige Spalten, CE-Zeichen aus DXF). Abweichung zwischen Original
  und LightBurn-Nachbildung: max. ca. **0,44 mm auf 108 mm Zeilenlänge**.
- Vom Autor unter Windows konvertiert und die Struktur gegen eine von Hand
  erstellte LightBurn-Datei verglichen. Positionen bei 100 % identisch zur
  Erwartung.
- Oberfläche und Vorschau automatisiert unter Linux (Xvfb) getestet, vom Autor
  unter Windows 11 genutzt.
- **Nicht** systematisch in LightBurn selbst gegengeprüft: Die Textanker-Formeln
  stammen aus von LightBurn gespeicherten BackupPaths.

---

## Mögliche nächste Schritte

Ideen, nicht umgesetzt:

- Installer mit Inno Setup (`--onedir` plus Versionsinfos), damit sich nichts zur
  Laufzeit entpackt und Virenscanner seltener anschlagen
- GitHub Actions: `.exe` auf einem Windows-Runner bauen und an Releases anhängen
- Code-Signatur gegen die SmartScreen-Warnung
- Unit-Tests mit synthetischen EZD-Dateien

---

## Technische Dokumentation

Details zum EZD-Dateiformat, zur Abbildung von EZCAD-Text auf LightBurn-Text,
zur `.lbrn2`-Struktur und zur Layerzuordnung:
**[docs/format-notes.md](docs/format-notes.md)**

Änderungen je Version: **[CHANGELOG.md](CHANGELOG.md)**

Der Quelltext liegt vollständig in der einen Datei `ezd2lbrn.py` (ca. 3000
Zeilen) und ist in dieser Reihenfolge aufgebaut: EZD-Parser, Geometrie,
Schriftmetriken, Vorlagen-Vermessung, Vorschau-Daten, Konverter, Oberfläche,
Kommandozeile.

---

## Lizenz

**MIT-Lizenz** – Copyright (c) 2026 Christopher Schmick, iot-simply.com.
Frei nutzbar, veränderbar und weitergebbar, solange Copyright- und
Lizenzhinweis erhalten bleiben. Ohne Gewährleistung. Siehe [LICENSE](LICENSE).

Der EZD-Leseteil (Huffman-Dekompression, Objektstruktur, Feldbelegung) basiert
auf dem EZD-Parser von [MeerK40t](https://github.com/meerk40t/meerk40t)
(MIT-Lizenz) und wurde für ezd2lbrn neu geschrieben und erweitert.

Die Lizenztexte liegen auch der `.exe` bei – Schaltfläche „Lizenzen“ unten
rechts im Programm. Auf der Kommandozeile `ezd2lbrn.exe --lizenzen > lizenzen.txt`:
Die `.exe` wird ohne Konsolenfenster gebaut, die Ausgabe muss deshalb in eine
Datei umgeleitet werden. Zu Markenrechten siehe [Marken](#marken).

Lizenzen aller verwendeten Fremdkomponenten:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Die **vollständigen**
Lizenztexte sind im Programm eingebettet – Schaltfläche „Lizenzen“ unten rechts
oder:

```bash
python ezd2lbrn.py --lizenzen
```

---

## English summary

**ezd2lbrn** converts EZCAD2 project files (`.ezd`) to LightBurn projects
(`.lbrn2`), fully offline. Unlike online converters, **text stays editable text
in LightBurn** instead of being turned into curves: font, bold, height,
position, alignment, line spacing and character spacing are carried over.
Hatches become fill (scan) layers, contours become paths, and EZCAD pens become
LightBurn layers. An existing `.lbrn2` can be used as a template for device and
laser parameters, and optionally for size and placement.

The user interface, all messages and the command-line options are in **German**.
Quick start:

```bash
pip install -r requirements.txt
python ezd2lbrn.py                          # graphical interface
python ezd2lbrn.py file.ezd --feld 110      # command line, 110 mm galvo field
python ezd2lbrn.py --help                   # all options
```

Requires Python 3.9+ with tkinter; `fonttools` and `pillow` are optional.
On Windows, `build_exe.bat` builds a standalone `ezd2lbrn.exe` with PyInstaller.
Not supported: bitmaps, barcodes, arrays, control objects, variable text,
rounded rectangle corners, elliptical arcs, italic text, EZCAD2 JSF/SHX fonts
(use font substitution). EZCAD2 only, not EZCAD3.

Licensed under the **MIT License**, Copyright (c) 2026 Christopher Schmick,
iot-simply.com.
