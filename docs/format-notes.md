# Technische Grundlagen

Notizen zum EZD-Dateiformat, zur Abbildung von EZCAD-Text auf LightBurn-Text,
zur `.lbrn2`-Struktur und zur Layerzuordnung. Grundlage der Umsetzung in
`ezd2lbrn.py`.

Die Angaben stammen aus der Analyse echter Dateien und wurden gegen
Beispieldateien geprüft. Sie sind keine offizielle Dokumentation der Hersteller.

---

## 1. EZD-Format (EZCAD2)

### Dateikopf

- Kennung `EZCADUNI` (UTF-16).
- Ab Offset **0x158** folgt eine Tabelle mit Sprungadressen auf die Abschnitte:
  Vorschau, Stifte, Schriften, Vektordaten.
- Die **Vektordaten sind Huffman-komprimiert** (Codelänge max. ca. 9 Bit).
  ezd2lbrn dekomprimiert tabellengesteuert, was deutlich schneller ist als
  bitweises Durchlaufen des Baums.

### Objektstruktur

Jedes Objekt besteht aus einem **Typ-Marker (int32)**, einem **Kopf mit 15
Feldern** und anschließend typabhängigen Feldern.

| Typ | Bedeutung |
|---|---|
| `1` | Kurve |
| `3` | Rechteck |
| `4` | Kreis |
| `5` | Ellipse |
| `6` | Polygon |
| `0x10` | Gruppe |
| `0x20` | Schraffur |
| `0x30` | Kombination |
| `0x40` | Bild |
| `0x50` | Vektordatei (importiertes DXF/SVG) |
| `0x60` | Spirale |
| `0x800` | Text |
| `0x2000` – `0x6000` | Steuerobjekte (Timer, IO, Achse) |

Bei unbekannten Objekten wird über einen **Resync** aufgesetzt, statt die ganze
Datei zu verwerfen – der Parser ist bewusst fehlertolerant.

### Text-Felder

| Feld | Bedeutung |
|---|---|
| `1` | Höhe |
| `2` | Zeichenbreite |
| `6` | Zeilenlücke |
| `7` | Bezugspunkt – **Grundlinie der letzten Zeile** |
| `8` | Ausrichtung: `0` links, `1` mittig, `2` rechts |
| `9` | Matrix 3×3 |
| `10` | Text |
| `18` | Schrift |
| `97` | Fett |

### Geschrafter Text

Auf ein Textobjekt folgt eine **Schraffur mit gleicher Position**. Deren Kinder
enthalten die Glyphen-Umrisse, und zwar **eine Gruppe je sichtbarem Zeichen**.
Diese exakte Geometrie wird für Ausrichtung und Zeichenabstand ausgewertet –
daher stimmt die Umsetzung auch dann, wenn die Schriftmetrik abweicht.

### Stift-Felder

| Feld | Bedeutung |
|---|---|
| `1` | Name |
| `4` | Durchgänge |
| `5` | Geschwindigkeit |
| `6` | Leistung in % |
| `7` | Frequenz in Hz |
| `8` | Pulsbreite |
| `9` / `10` | Ein- / Aus-Verzögerung |
| `12` | Sprunggeschwindigkeit |

### Schraffur

- **Bit `0x400`** im Typfeld bedeutet **Kreuzschraffur**.
- Linienabstand und Winkel stammen aus **Schraffur 1**.

### Herkunft des Leseteils

Grundlage ist der EZD-Parser von **MeerK40t** (MIT-Lizenz), für ezd2lbrn neu
geschrieben und erweitert.

---

## 2. Abbildung Text: EZCAD → LightBurn

Diese Formeln wurden ermittelt und gegen Beispieldateien geprüft. Sie sind der
Kern des Konverters.

### Texthöhe

```
H = Höhe × sy
```

`sy` ist die Länge der Y-Spalte der Matrix. Das LightBurn-Attribut `H`
entspricht der **Em-Größe**.

### Horizontale Skalierung

EZCAD rechnet die Zeichenbreite wie Windows GDI:

```
sx = (Breite / Höhe) / (xAvgCharWidth / unitsPerEm)
```

Fallback-Werte ohne fontTools: Arial `904/2048`, Arial Bold `983/2048`.
Der Wert wird als **XForm-Skalierung des Textes** geschrieben, nicht als
Zeichenabstand.

### Zeilenabstand

EZCAD-Abstand:

```
pitch = (Höhe + Zeilenlücke) × sy
```

LightBurn rechnet dagegen:

```
pitch = k × H × (1 + LnS / 100)
```

mit `k = (usWinAscent + usWinDescent) / unitsPerEm`. Daraus wird `LnS`
bestimmt.

### Vertikaler Anker

Bei LightBurn `Av=1` gilt für `n` Zeilen:

```
Grundlinie_i = Anker + ((n − 1) / 2 − i − 0,5) × pitch
```

Da EZCAD die **Grundlinie der letzten Zeile** liefert, folgt:

```
Anker_y = Grundlinie_letzte + n × pitch / 2
```

### Horizontaler Anker

LightBurn richtet an der **sichtbaren Glyphenkante** aus:
`Ah = 0` linke Kante, `1` Mitte, `2` rechte Kante. Die tatsächliche Kante wird
aus den Glyphen-Umrissen der zugehörigen Schraffur gewonnen.

### Rest-Zeichenabstand

`LS` (Prozent der Höhe) wird aus der EZD-Glyphenbreite im Vergleich zur
Schriftmetrik berechnet. **Werte unter 0,75 % werden auf 0 gesetzt**, um
unnötiges Rauschen in der Ausgabedatei zu vermeiden.

### Koordinaten

Die EZCAD-Mitte (0/0) wird auf die **Feldmitte** gelegt. Y zeigt in beiden
Formaten nach oben.

---

## 3. LightBurn `.lbrn2`

### Text

```xml
<Shape Type="Text" Font="Arial,-1,4096,5,700,0,0,0,0,0" ... />
```

- `Font` im **Qt-6-Format**, Gewicht `400` (normal) bzw. `700` (fett)
- weitere Attribute: `Bold`, `H`, `LS`, `LnS`, `Ah`, `Av`, `Weld`
- `HasBackupPath="0"` – LightBurn erzeugt die Kurvenkopie beim ersten Speichern
  selbst

### Pfade

- `VertList` mit Einträgen `V x y` plus Kontrollpunkten `c0x`/`c0y` (ausgehend)
  und `c1x`/`c1y` (eingehend). `c0x1` bedeutet: **kein Kontrollpunkt**.
- `PrimList` mit `L i j` (Linie) und `B i j` (Bezier).

### Layer

- `CutSetting type="Scan"` für Schraffur bzw. Fill
- `CutSetting type="Cut"` für Linien
- Felder unter anderem: `maxPower`, `speed`, `frequency`, `QPulseWidth`,
  `LaserOn_TC`, `End_TC`, `JumpSpeed`, `numPasses`, `interval`, `angle`,
  `crossHatch`

---

## 4. Layerzuordnung

- **EZCAD-Stift N → LightBurn-Layer N.**
- Weitere Varianten desselben Stifts (z. B. ein abweichender Schraffurwinkel)
  bekommen freie Layer-Indizes.
- **Maximal 30 Layer.**

---

## 5. Größe und Lage

Drei Modi:

1. **Feste Skalierung** in Prozent.
2. **Auf Feld einpassen** – Skalierung um die EZCAD-Mitte, mit Rand, **nur
   verkleinern**.
3. **Größe und Lage aus Vorlage** – proportional in den Inhaltsrahmen der
   Vorlage, mittig, mit optionalem Rand.

Der **Schraffur-Linienabstand wird bewusst nie mitskaliert**, da er ein
physikalischer Laserparameter ist.

---

## 6. Vorlage

Eine `.lbrn2`-Vorlage liefert:

- **Gerätedaten**: Root-Attribute, `UIPrefs`, `GalvoRotaryConfig`,
  `VariableText`
- **Laserparameter je Layer-Index**

Linienabstand, Winkel und Kreuzschraffur kommen dagegen **immer aus der
EZD-Datei**.

Der Inhaltsrahmen der Vorlage wird von `template_extent()` gemessen: Pfade,
BackupPaths von Texten, `Rect`/`Ellipse`/`Bitmap`, einschließlich verschachtelter
Gruppen und ihrer XForms. Daher muss die Vorlage einmal in LightBurn
gespeichert worden sein – erst dann existieren die BackupPaths der Texte.

---

## 7. Schriftmetriken

`FontMetrics` (über fontTools) durchsucht die installierten Schriften
(Windows-Schriftordner, Benutzerschriften, Linux- und macOS-Ordner) und liefert:

- `k = (usWinAscent + usWinDescent) / unitsPerEm`
- `xAvgCharWidth`
- Glyphenbreiten

Ohne fontTools greifen die oben genannten Arial-Fallback-Werte.
