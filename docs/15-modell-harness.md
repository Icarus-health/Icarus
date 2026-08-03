# Modell-Harness: Routing, Fallback und Evaluation

## Ziel

Modelle sind austauschbare Laufzeitmotoren. Das Selbstmodell, die Nutzeridentität und die verbindlichen Daten bleiben außerhalb der Anbieterprofile. Normale Nutzer wählen kein Modell; Icarus wählt innerhalb expliziter Grenzen.

## Aktivierung

Ohne `ICARUS_MODEL_ROUTES` gilt der bisherige Einzelanbieterweg unverändert.

Mit `ICARUS_MODEL_ROUTES` wird eine JSON-Liste von Modellprofilen geladen. Ein kommentiertes Beispiel liegt unter `schema/model-registry.example.json`.

Jedes Profil enthält:

- interne Profil-ID
- Anbieter und Modellversion
- Aufgabenfähigkeiten
- Kontextgrenze
- Werkzeugfähigkeit
- lokale oder entfernte Ausführung
- gemessene Qualitätswerte je Aufgabenprofil
- erwartete Latenz
- Eingabe- und Ausgabekosten
- Datenschutzklasse

Die Registry speichert keine Personendaten und keine Nutzeridentität.

## Aufgabenprofile

- Gespräch
- Planung
- Recherche
- Verdichtung
- Code
- Dokumente
- Klassifikation

## Routingregeln

Ein Kandidat wird zunächst hart ausgeschlossen, wenn:

- das Aufgabenprofil nicht unterstützt wird,
- die Kontextgrenze nicht reicht,
- benötigte Werkzeuge fehlen,
- die Datenschutzklasse nicht genügt,
- die Mindestqualität unterschritten wird,
- Latenz- oder Kostenlimit überschritten werden,
- sein Circuit Breaker offen ist.

Unter den verbleibenden Modellen gilt:

1. eine ausdrücklich gesetzte Expertenpräferenz,
2. lokale Ausführung, wenn sie höchstens innerhalb der Qualitätsmarge unter dem besten Kandidaten liegt,
3. Qualität,
4. Latenz,
5. Kosten,
6. stabile Modell-ID als deterministischer Gleichstandsbrecher.

Für `secret`, `highly_sensitive` und `local_only` werden ausschließlich lokale Modelle zugelassen. Für `sensitive` oder `confidential` sind lokale und ausdrücklich vertrauenswürdige entfernte Profile zulässig. Ein Fallback darf diese Grenze niemals lockern.

## Fallback und Circuit Breaker

Scheitert ein geeigneter Provider, wird der nächste bereits vorab zulässige Kandidat versucht. Nach der konfigurierten Fehlerzahl öffnet der Circuit für eine Abkühlzeit. Währenddessen wird das Modell nicht angesprochen.

Jeder Auswahl-, Fehler- und Erfolgsfall kann über einen Audit-Sink protokolliert werden. Der Eintrag enthält Profil, Modell-ID, Anbieter, Modellversion, Gründe, geschätzte Kosten, Fallbackrang und Latenz – jedoch keine Gesprächsinhalte.

## Budget

`UsageBudget` prüft Kosten-, Eingabe- und Ausgabetokenlimits **vor** einem Provideraufruf. Ein Aufruf, der das Budget überschreiten würde, findet nicht statt.

Umgebungsvariablen:

- `ICARUS_MODEL_MAX_COST`
- `ICARUS_MODEL_MAX_INPUT_TOKENS`
- `ICARUS_MODEL_MAX_OUTPUT_TOKENS`

## Evaluation

`Evaluator` führt versionierte Icarus-Aufgaben gegen alle passenden Registry-Modelle aus. Ein domänenspezifischer Scorer bewertet die Antwort. Der Bericht enthält pro Modellversion:

- Fallzahl
- Erfolgsquote
- mittlere Qualität
- mittlere Latenz
- geschätzte Kosten
- Einzelergebnisse und Fehler

Qualitätswerte in der Registry sind keine Anbieterbehauptung. Sie müssen aus solchen eigenen Evaluationen stammen und bei Modellversionswechseln neu erhoben werden.

## Grenzen

- Tokenmengen und Kosten sind vor dem Aufruf Schätzungen, solange ein Provider keine tatsächlichen Nutzungsdaten zurückliefert.
- Ein Evaluationsdatensatz mit echten, freigegebenen Icarus-Aufgaben muss organisatorisch aufgebaut und versioniert werden.
- Der Alltag bleibt modellagnostisch. Registry, Budgets und Auswertungen gehören in eine Expertenansicht.
