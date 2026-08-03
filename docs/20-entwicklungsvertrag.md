# Modellneutraler Entwicklungsvertrag

> **Status:** verbindlich für neue Entwicklungsarbeit
> **Stand:** 2026-08-03
> **Betroffene Komponenten:** gesamtes Repository
> **Zuletzt gegen Code und Produktvision geprüft:** 2026-08-03

## Ziel

Icarus soll mit einem begrenzten Budget zuverlässig wachsen. Hochwertige
Entscheidungszeit wird deshalb dort eingesetzt, wo ein Fehler teuer und später
schwer zu erkennen wäre. Wiederholbare Umsetzung wird an günstigere,
qualifizierte Modelle und deterministische Werkzeuge abgegeben.

Der Vertrag ist absichtlich modellneutral. Neue Modelle erscheinen laufend;
dauerhaft sind nur Rollen, messbare Eignung und Qualitätsgrenzen.

## Die drei Rollen

### Frontier-Modell

Wird eingesetzt für:

- Produkt- und Architekturentscheidungen mit mehreren vernünftigen Wegen;
- Änderungen an Gedächtnis, Migration, Backup, Geheimnissen, Policy,
  Freigaben, Browsergrenzen oder dauerhaften Workflows;
- Zerlegung großer Vorhaben in kleine Aufgabenpakete;
- unabhängige Prüfung risikoreicher Änderungen;
- Ursachenanalyse, wenn Tests den Fehler nicht eindeutig eingrenzen.

### Ausführungsmodell

Setzt ein bereits geprüftes, kleines Aufgabenpaket um. Es darf innerhalb der
erlaubten Dateien entscheiden, aber weder Ziel, Sicherheitszusagen noch
Architektur erweitern. Es wird nur für eine Risikoklasse eingesetzt, für die es
den aktuellen Icarus-Eignungstest bestanden hat.

Coding in einem allgemeinen Chat ist dafür ausdrücklich zulässig. Entscheidend
ist nicht die Oberfläche, in der Code entsteht, sondern ob Branch, Scope,
Tests, Review und Rückverfolgbarkeit dieselben Regeln erfüllen. Chat-Code wird
nie direkt in eine Integrationslinie übernommen.

### Deterministische Werkzeuge

Tests, Linter, Schema-Prüfungen, Browserläufe, Builds und Restore-Proben sind die
unbestechliche dritte Rolle. Modelle schlagen vor; diese Werkzeuge liefern den
reproduzierbaren Nachweis.

## Risikoklassen

### A – existenziell

Ein Fehler kann Gedächtnis verlieren, Daten offenlegen, eine Außenwirkung
auslösen, eine Freigabe umgehen oder eine Installation dauerhaft beschädigen.

Beispiele: Schema und Migration, Backup/Restore, Verschlüsselung und Keychain,
Policy, Freigaben, Browser- und Dateigrenzen, Workflow-Wiederanlauf, Signierung
und Updates.

**Besetzung:** Frontier-Planung, Frontier-Umsetzung und unabhängiger
Frontier-Review. Deterministische Spezialgates sind Pflicht.

### B – produktrelevant

Ein Fehler stört einen Kernablauf, ist aber ohne Datenverlust oder unbemerkte
Außenwirkung behebbar.

Beispiele: normale API-Endpunkte, Adapter, Priorisierung, Projektionen,
Alltagsansichten und rein lesende Connectoren.

**Besetzung:** Frontier-geprüftes Aufgabenpaket, qualifiziertes
Ausführungsmodell, unabhängiger Review. Mindestens jeder fünfte B-PR wird
zusätzlich durch ein Frontier-Modell tief geprüft.

### C – mechanisch

Die gewünschte Änderung ist vollständig bestimmt und die Fehlerfläche klein.

Beispiele: klar abgegrenzte Texte, Umbenennungen, Testdaten, Formatierung und
bereits spezifizierte kleine Komponenten.

**Besetzung:** qualifiziertes Ausführungsmodell, deterministische Gates und
stichprobenartiger Review.

## Verbindlicher Ablauf

1. **Problem belegen.** Reproduktion, Nutzerwirkung und Nicht-Ziele notieren.
2. **Risiko einstufen.** Die höchste berührte Grenze bestimmt die Klasse.
3. **Aufgabenpaket prüfen.** Ziel, erlaubte Pfade, verbotene Änderungen,
   Akzeptanzkriterien und Befehle müssen vollständig sein.
4. **Kleinen Branch anlegen.** Keine direkte Arbeit auf `main` oder einer
   Integrationslinie.
5. **Umsetzen.** Nur den Auftrag bearbeiten; neue Erkenntnisse werden als
   Folgepaket festgehalten.
6. **Lokal nachweisen.** `make verify` und alle paketbezogenen Gates ausführen.
7. **Unabhängig reviewen.** Der Reviewer erhält Problem, Paket und Diff, nicht
   die Begründungskette des Implementierers.
8. **Als Draft-PR öffnen.** Erst nach grünen Gates und geschlossenen
   Risikofragen reviewbereit markieren.
9. **Integriert prüfen.** Nach Merge in die Integrationslinie denselben
   Kernablauf erneut ausführen. Modulgrün ist nicht Systemgrün.
10. **Reale Abnahme.** Consumer-Funktionen werden mit normalen Nutzern und
    echter Auslieferung geprüft; ein Modell darf diese Abnahme nicht simulieren.

## Budgetregeln

- Frontier-Zeit wird für Planung und Grenzen gebündelt, nicht für wiederholte
  Formatierung oder vorhersehbare Einzeländerungen.
- B- und C-Aufgaben bleiben klein genug, dass ein frischer Kontext genügt.
- Fehlgeschlagene Ausführungsversuche werden nicht mit immer längeren Prompts
  gerettet. Nach zwei Fehlversuchen wird das Paket oder die Risikoklasse neu
  geprüft.
- Reviews prüfen mehrere kleine PRs gebündelt, aber jeder PR bleibt einzeln
  rückrollbar.
- Modellrollen werden regelmäßig neu qualifiziert. Ein früher bestandener Test
  ist kein dauerhafter Freifahrtschein.

## Qualifikation von Ausführungsmodellen

Die Rollenfähigkeit für B- und C-Aufgaben wird mit der versionierten,
eingefrorenen Suite unter `tasks/qualification/` gemessen. Sie verwendet zehn
synthetische Mini-Aufgaben, getrennte deterministische Tests und feste
Zeitlimits. Der Lauf erfolgt ohne Netzwerk, reale Konten, Schlüssel oder
Nutzerdaten.

Die Suite bewertet Korrektheit, Testqualität, Scope-Treue, Sicherheit und
Dokumentation. Ein kritischer Scope- oder Sicherheitsverstoß führt unabhängig
von der Punktzahl zum Nichtbestehen. Das zu prüfende Modell bewertet weder sich
selbst noch seine Einreichung.

Ein Bericht hält mindestens Suite-Version, Commit, UTC-Datum, Laufkennung,
Rollenklasse, Laufzeit und Kosten fest. Er weist ausschließlich die aktuelle
Rollenfähigkeit aus; eine dauerhafte Rangliste oder ein Modellname wird nicht
Teil dieses Vertrags. Jede inhaltliche Änderung an Aufgaben, Tests, Gewichtung
oder Bestehensgrenze erhöht die Suite-Version und verlangt eine neue
Qualifikation.

Die Suite kann höchstens B oder C ausweisen. Klasse A bleibt unabhängig vom
Ergebnis Frontier-Planung, Frontier-Umsetzung und Frontier-Review vorbehalten.
Der deterministische Selbsttest läuft mit `make qualify-execution-model`.

## Was die bestehenden agentisch erzeugten PRs zeigen

Die aktuelle Produktlinie enthält umfangreiche, nachvollziehbare Module und
viele gute Tests. Gleichzeitig liegen die verbleibenden Risiken vor allem an
den Übergängen zwischen UI, Laufzeit, Freigabe und Betrieb. Daraus folgt keine
Ablehnung von Chat- oder Agentencode. Es folgt die Pflicht, Systemgrenzen mit
integrierten Tests zu prüfen und generierten Code nie wegen seiner Herkunft als
fertig anzusehen.

## Steuerungskennzahlen

Monatlich werden nur wenige, belastbare Werte betrachtet:

- Anteil grüner PRs beim ersten vollständigen CI-Lauf;
- Rückläufer wegen Scope-Verstoß oder fehlender Akzeptanzkriterien;
- nach Merge gefundene Fehler nach Risikoklasse;
- mittlere Frontier-Zeit pro ausgeliefertem Aufgabenpaket;
- bestandene Icarus-Eignungstests je Modellrolle;
- Backup-/Restore- und Freigabe-End-to-End-Erfolgsquote.

Kostenersparnis ist nur ein Erfolg, wenn diese Qualitätswerte stabil bleiben.
