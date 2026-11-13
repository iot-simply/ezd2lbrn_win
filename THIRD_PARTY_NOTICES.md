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

In der grafischen Oberfläche: Schaltfläche **„Lizenzen“** unten rechts.

## Hinweis zu PyInstaller

PyInstaller wird ausschließlich zum Bauen der eigenständigen `ezd2lbrn.exe`
verwendet und ist kein Bestandteil des Quelltextes. Die
Bootloader-Ausnahme der PyInstaller-Lizenz erlaubt ausdrücklich, damit
erzeugte Programme unter beliebiger Lizenz weiterzugeben – ezd2lbrn bleibt
also auch als `.exe` MIT-lizenziert.
