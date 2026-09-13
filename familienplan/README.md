# Familien-Dienstplaner

Konsolenprogramm für die Schicht- und Betreuungsplanung der Familie.
Reines Python 3 aus der Standardbibliothek – auf dem Mac ist alles Nötige
bereits vorhanden, es muss nichts installiert werden.

## Start

```sh
cd ~/familienplan
./familienplan              # interaktives Menü
./familienplan --help       # alle Kommandos
```

Falls `python3` fehlt: einmalig `xcode-select --install` ausführen.

Bequemer Aufruf von überall (einmalig in `~/.zshrc` eintragen):

```sh
alias dienstplan="~/familienplan/familienplan"
```

## Kommandos

| Kommando | Wirkung |
|---|---|
| `./familienplan neu --start 01.11.2026 --wochen 4` | Plan neu berechnen |
| `./familienplan neu --variante rotierend` | Plan mit anderer Strategie |
| `./familienplan plan` | große Tabelle mit 24-h-Abdeckungsbalken |
| `./familienplan plan --woche 2` | nur eine Woche |
| `./familienplan kompakt` | Kompakttabelle (f/s/n) |
| `./familienplan kompakt --zeile` | eine Zeile pro Woche, zum Kopieren |
| `./familienplan kompakt --plain` | ohne Rahmen/Farben (WhatsApp, Zettel) |
| `./familienplan betreuung` | Betreuungslücken + Budget für Fremdbetreuung |
| `./familienplan check` | Prüfung aller Regeln |
| `./familienplan tag 12.11.2026` | ein Tag in 30-Minuten-Auflösung |
| `./familienplan setze Maria 12.11.2026 f` | Dienst von Hand ändern (`frei` = freinehmen) |
| `./familienplan vergleich` | drei Szenarien nebeneinander |
| `./familienplan export --format csv` | Tabelle für Numbers/Excel |
| `./familienplan export --format ics` | Kalenderdatei für Apple Kalender |
| `./familienplan config --bearbeiten` | Einstellungen als JSON exportieren |

## Kürzel

```
f  Frühdienst      s  Spätdienst      n  Nachtdienst
·  frei            >  Nachtdienst des Vortags läuft noch bis 08:00
```

## Regeln, die das Programm einhält

Harte Regeln (werden nie verletzt):

* Manuel: 3 Dienste/Woche, **jede** Schicht mindestens einmal
* Manuel: mindestens 12 h Ruhe zwischen zwei Diensten (auch über den Wochenwechsel)
* Maria: 3 Frühdienste/Woche
* Tochter: durchgehend betreut; Lücken höchstens 8 h pro Woche (Budget Fremdbetreuung)
* eingetragene Wunschfrei-Tage

Weiche Ziele (werden gewichtet optimiert):

* möglichst gar keine Betreuungslücke
* keine Alleinbetreuung in der Erholungsphase nach Nacht- oder Spätdienst
* Wochenenddienste gleichmäßig verteilt
* Dienste am Stück statt über die Woche verstreut
* gemeinsame freie Tage für die Familie

## Planungsvarianten

| Variante | Idee |
|---|---|
| `lueckenfrei` (Standard) | Betreuungslücken strikt vermeiden, ruhiger Rhythmus |
| `familienzeit` | möglichst viele Tage, an denen niemand arbeitet |
| `rotierend` | Wochenenddienste rotieren, kleine Lücken im Budget erlaubt |

## Einstellungen ändern

Alle Zeiten, Stunden und Regeln stehen in `~/.familienplan/plan.json`
(Abschnitt `config`) und können direkt bearbeitet werden – oder über:

```sh
./familienplan config --bearbeiten
```

Wichtige Stellschrauben:

* `schichten` – Anfangs-/Endzeiten und `netto_h` (bezahlte Arbeitszeit je Dienst)
* `vertrag_h_woche`, `dienste_pro_woche`, `min_ruhe_h`
* `wunschfrei` – Liste von Datumsangaben, z. B. `["2026-11-14"]`
* `externe_hilfe.max_h_pro_woche` – Budget für Oma/Opa
* `kita` – falls später Betreuungszeiten dazukommen: `"aktiv": true`

## Annahmen

* **Woche** = 7-Tage-Block ab Startdatum (01.11.2026 ist ein Sonntag),
  nicht die Kalenderwoche.
* **Außer-Haus-Zeit ≠ Arbeitszeit.** Die genannten Zeiten sind Abwesenheit
  (Weg, Übergabe, Pause). Die Vertragsstunden werden separat über `netto_h`
  gezählt: Manuel f 6,5 h + s 7,0 h + n 6,5 h = 20 h; Maria 3 × 5,0 h = 15 h.
* Wenn ein Elternteil zu Hause ist, gilt die Tochter als betreut.
