# Änderungsverlauf

Alle nennenswerten Änderungen an ezd2lbrn. Versionsnummern entsprechen der
Konstanten `VERSION` in `ezd2lbrn.py`.

## 1.8.2

- **Eigenes Programmsymbol**: ein schwarz eloxiertes Typenschild mit gravierter
  Marke – links die EZCAD-Schraffur, rechts die LightBurn-Füllung. Bisher
  diente das iot-simply-Logo als Programmsymbol; es bleibt im Kopfbereich des
  Info-Fensters.
- Im dunklen Design wurden Auswahlfelder und Eingabefelder beim Überfahren mit
  der Maus hell, die Schrift war dann nicht mehr lesbar. Die Zustände „active“
  und „hover“ folgen jetzt dem Design; ohne sie nimmt das ttk-Thema „clam“
  seine eigene helle Farbe.
- Ein bereits geöffnetes Info-Fenster färbt sich beim Umschalten des Designs
  sofort nach statt erst beim erneuten Öffnen.

## 1.8.1

- Die Fenster-Titelleiste folgt unter Windows jetzt dem dunklen Design
  (DwmSetWindowAttribute ab Windows 10 1809). Tk zeichnet diese Leiste nicht
  selbst, deshalb blieb sie bisher hell.
- Fußzeile zeigt nur noch Name und Version ohne Beschriftung, das Info-Fenster
  sagt „Version" statt „Fassung".

## 1.8

- **Info-Fenster** neu: Typenschild als Kopf, Version, Zweck, Gefahrenhinweis,
  Copyright, anklickbare Webseite, Marken und Ablageort der Einstellungen.
- **Dunkles Design** neben dem hellen, umschaltbar im Info-Fenster; die Wahl
  wird gespeichert.
- Das Typenschild ist aus dem Hauptfenster verschwunden, der Arbeitsbereich
  gewinnt dadurch rund 90 Pixel. In der Fußzeile stehen nur noch Name, Version,
  Lizenzen und Info, darüber ein dauerhafter Gefahrenhinweis in Rot.
- **Lizenzfenster**: Abschnittswähler zum Springen und „Als Datei speichern“.
- Eingebettete Lizenztexte um die Abschnitte **MARKEN** und **IHRE DATEIEN**
  ergänzt.
- **`--selbsttest`** eingebaut: baut die Fenster auf, wandelt einen kleinen
  Entwurf bis zur fertigen `.lbrn2` um und prüft die Lizenztexte. Der
  Bau-Workflow führt ihn gegen die fertige `.exe` aus.
- Oberfläche, Meldungen und Dokumentation durchgehend mit echten Umlauten.
- Alle Konsolenausgaben laufen über eine Funktion, die fehlende Ausgabeströme
  stillschweigend hinnimmt – in der `--noconsole`-exe gibt es keine.
- Release-Text nennt jetzt Größe und SHA-256 der Datei; der Workflow hat
  Zeitgrenzen je Schritt und für den gesamten Auftrag.

## 1.7.1

- Markenzuordnung ergänzt: EZCAD ist eine Marke von Beijing JCZ Technology,
  LightBurn eine Marke von LightBurn Software. ezd2lbrn ist ein unabhängiges
  Projekt ohne Verbindung zu diesen Unternehmen. Der Hinweis steht im
  Info-Dialog, im Lizenzfenster und in der Dokumentation.
- Deutlicher Sicherheitshinweis im Info-Dialog: keine Gewährleistung,
  Laserparameter und Ergebnis vor dem ersten Lauf an Testmaterial prüfen.
- Dokumentation: Abschnitte „Haftung und Sicherheit“ und „Marken“ in der
  README, fertige Textbausteine für eine Downloadseite unter
  `docs/download-hinweise.md`.
- Keine Änderung an der Umwandlung selbst: Parser, Konverter und Ausgabe sind
  gegenüber 1.7 unverändert.

## 1.7

- Umstellung auf die **MIT-Lizenz** (Copyright (c) 2026 Christopher Schmick,
  iot-simply.com).

## 1.6.5 – 1.6.6

- Vollständige Lizenztexte der Drittkomponenten ergänzt und ins Programm
  eingebettet: Fenster „Lizenzen“ und Aufruf `--lizenzen`.

## 1.6.4

- Name und Webadresse des Autors in Fußzeile und Info-Bereich.

## 1.6 – 1.6.3

- iot-simply-Logo und Programmsymbol fest ins Programm eingebettet.
- Abschnitts-Layout der Oberfläche korrigiert.
- Schaltflächen der Vorschau überarbeitet („Speichern“ entfernt).

## 1.5

- Neues Design: ttk-Oberfläche („clam“), Kopfbereich als schwarz eloxiertes
  Typenschild, farbiges Log.

## 1.4

- **Vorschau-Fenster**: Quelle, Ziel und überlagerte Ansicht, Feld- und
  Vorlagenrahmen, Zoom und Verschieben, PNG-Export.

## 1.3

- Einstellbarer Rand zum Vorlagen-Kasten.

## 1.2

- Letzte Speicherorte und Einstellungen werden gemerkt
  (`%APPDATA%\ezd2lbrn\einstellungen.json`).
- Schriftersetzung mit Auswahllisten: Quelle aus der EZD-Datei, Ziel aus den
  installierten Schriften.
- Größe und Lage aus der Vorlage übernehmen.

## 1.1

- „Auf Feld einpassen“ mit einstellbarem Rand sowie feste Skalierung in Prozent.

## 1.0

- Erste Version: EZD-Parser, Texte als bearbeitbare LightBurn-Texte,
  Schraffur → Fill, Stifte → Layer, Vorlage für Geräte- und Laserparameter,
  grafische Oberfläche und Kommandozeile.
