# Textbausteine für die Downloadseite

Fertiger Text zum Übernehmen auf iot-simply.com. Inhaltlich deckt er ab, was
bei der kostenlosen Weitergabe der `.exe` genannt sein sollte: Herkunft,
Lizenz, Marken, SmartScreen und Sicherheit.

Rechtlicher Hinweis zu diesem Dokument: Es ist eine Arbeitshilfe, keine
Rechtsberatung.

---

## Vorschlag für die Seite

### ezd2lbrn – EZCAD2-Dateien für LightBurn

Wandelt EZCAD2-Projektdateien (`.ezd`) in LightBurn-Projekte (`.lbrn2`) um,
komplett offline. Anders als Online-Konverter behält ezd2lbrn **Texte als
bearbeitbare LightBurn-Textobjekte**, statt sie in Kurven zu verwandeln.
Schraffuren werden zu Fill-Layern, EZCAD-Stifte zu Layern.

**Kostenlos und quelloffen (MIT-Lizenz).** Kein Konto, keine Registrierung,
keine Datenübertragung – das Programm arbeitet ausschließlich lokal.

**Download:** [ezd2lbrn.exe (Windows, 64 Bit)](https://github.com/iot-simply/ezd2lbrn_win/releases/latest)
**Quelltext:** [github.com/iot-simply/ezd2lbrn_win](https://github.com/iot-simply/ezd2lbrn_win)

Systemvoraussetzung: Windows 64 Bit. Python wird **nicht** benötigt, es ist in
der Programmdatei enthalten.

Der Release-Text auf GitHub nennt Größe und SHA-256 der Datei. Wer mag, prüft
den Download damit (PowerShell im Download-Ordner):
`Get-FileHash .\ezd2lbrn.exe -Algorithm SHA256`. Meldet Windows beim Start
`Could not load PyInstaller's embedded PKG archive`, war der Download
unvollständig – Datei löschen und neu laden.

---

#### Beim ersten Start: Windows-Hinweis bestätigen

Die Datei ist nicht mit einem Zertifikat signiert. Windows zeigt deshalb beim
ersten Start „Der Computer wurde durch Windows geschützt“:

1. Auf **Weitere Informationen** klicken
2. Dann auf **Trotzdem ausführen**

Beim Herunterladen kann der Browser zusätzlich melden, die Datei werde „nicht
häufig heruntergeladen“ – dort **Beibehalten** bzw. **Zulassen** wählen.
Alternativ in den Eigenschaften der Datei den Haken bei **Zulassen** setzen.

Wer das vermeiden möchte, baut die Programmdatei mit der beiliegenden
`build_exe.bat` selbst aus dem Quelltext.

---

#### Wichtig vor dem ersten Lauf

Das Programm steuert keinen Laser, es wandelt nur Dateien um. Die erzeugte
`.lbrn2` enthält aber Laserparameter aus der EZD-Datei oder aus Ihrer Vorlage.

**Prüfen Sie Geometrie, Größe, Position und alle Laserparameter in LightBurn
und testen Sie an Probematerial, bevor Sie ein Werkstück bearbeiten.** Falsche
Parameter können Werkstück, Optik oder Laserquelle beschädigen. Die
Sicherheitsregeln für den Betrieb Ihres Lasers gelten unverändert.

Die Darstellung in der Vorschau ist eine Nachbildung des LightBurn-Textsatzes.
Maßgeblich ist, was LightBurn selbst anzeigt.

---

#### Lizenz und Gewährleistung

ezd2lbrn steht unter der **MIT-Lizenz**, Copyright © 2026 Christopher Schmick,
iot-simply.com. Es darf frei genutzt, verändert und weitergegeben werden,
solange Copyright- und Lizenzhinweis erhalten bleiben.

Die Weitergabe erfolgt **kostenlos und ohne Gewährleistung**. Die vollständigen
Lizenztexte aller mitgelieferten Komponenten (MeerK40t, fontTools, Pillow,
Python, Tcl/Tk, PyInstaller) sind im Programm enthalten: Schaltfläche
**Lizenzen** unten rechts. Wer sie lieber als Datei hat:
`ezd2lbrn.exe --lizenzen > lizenzen.txt` in der Eingabeaufforderung – das
Programm hat kein Konsolenfenster, die Ausgabe muss deshalb umgeleitet werden.

---

#### Marken

EZCAD ist eine Marke von Beijing JCZ Technology Co., Ltd. LightBurn ist eine
Marke von LightBurn Software, LLC. ezd2lbrn ist ein unabhängiges Projekt, steht
in keiner Verbindung zu diesen Unternehmen und wird von ihnen weder unterstützt
noch gebilligt. Die Namen werden nur genannt, um die unterstützten
Dateiformate zu beschreiben.
