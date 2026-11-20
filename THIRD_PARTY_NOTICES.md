# Hinweise zu Fremdkomponenten

ezd2lbrn selbst steht unter der MIT-Lizenz (siehe [LICENSE](LICENSE)).
Darüber hinaus werden folgende Komponenten genutzt bzw. mitgeliefert:

| Komponente | Verwendung | Lizenz |
|---|---|---|
| [MeerK40t](https://github.com/meerk40t/meerk40t) | Grundlage des EZD-Leseteils (Huffman-Dekompression, Objektstruktur, Feldbelegung); für ezd2lbrn neu geschrieben und erweitert. Copyright (c) 2021 meerk40t | MIT |
| [fontTools](https://github.com/fonttools/fonttools) | Exakte Schriftmetriken (`unitsPerEm`, `usWinAscent`/`usWinDescent`, `xAvgCharWidth`, Glyphenbreiten) | MIT |
| [Pillow](https://python-pillow.org/) | Logo-Skalierung in der Oberfläche, PNG-Export der Vorschau | MIT-CMU (HPND) |
| [Python](https://www.python.org/) | Laufzeitumgebung; in der `.exe` mitgeliefert | PSF License |
| [Tcl/Tk](https://www.tcl.tk/) | Grafische Oberfläche über `tkinter`; in der `.exe` mitgeliefert | Tcl/Tk License (BSD-artig) |
| [PyInstaller](https://pyinstaller.org/) | Nur zum Bau der `.exe`; der mitgelieferte Bootloader steht unter einer eigenen Ausnahme-Regelung | GPL 2.0 mit Bootloader-Ausnahme |

## Vollständige Lizenztexte

Die **vollständigen** Lizenztexte aller hier genannten Komponenten sind
unverändert in `ezd2lbrn.py` eingebettet (Konstante `LICENSES_Z_B64`) und
lassen sich jederzeit anzeigen:

```bash
python ezd2lbrn.py --lizenzen
```

In der grafischen Oberfläche: Schaltfläche **„Lizenzen“** unten rechts. Dort
lassen sich die Texte auch über **„Als Datei speichern“** ablegen. Aus der
fertigen `.exe` heraus: `ezd2lbrn.exe --lizenzen > lizenzen.txt` (sie wird ohne
Konsolenfenster gebaut, die Ausgabe muss deshalb umgeleitet werden).

## Marken

ezd2lbrn ist ein eigenständiges Programm. Es besteht **keine Verbindung** zu
den Herstellern der genannten Programme, Steuerungen und Geräte; ezd2lbrn wird
von ihnen weder herausgegeben noch unterstützt, geprüft oder freigegeben.

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
Hersteller mitgeliefert. ezd2lbrn liest das EZD-Format und schreibt
LightBurn-Dateien, damit zwei eigenständige Programme Daten austauschen können.

## Ihre Dateien

An den Dateien, die Sie mit ezd2lbrn öffnen und erzeugen – Ihren EZD-Projekten,
den daraus entstehenden LightBurn-Dateien und Ihren Vorlagen – erwirbt niemand
Rechte. Sie bleiben vollständig Ihr Eigentum.

Das Programm arbeitet ausschließlich auf Ihrem Rechner. Es überträgt nichts ins
Internet, sendet keine Nutzungsdaten und benötigt keinerlei Anmeldung.

## Hinweis zu PyInstaller

PyInstaller wird ausschließlich zum Bauen der eigenständigen `ezd2lbrn.exe`
verwendet und ist kein Bestandteil des Quelltextes. Die
Bootloader-Ausnahme der PyInstaller-Lizenz erlaubt ausdrücklich, damit
erzeugte Programme unter beliebiger Lizenz weiterzugeben – ezd2lbrn bleibt
also auch als `.exe` MIT-lizenziert.
