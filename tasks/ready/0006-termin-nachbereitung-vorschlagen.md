# 0006 – Termine kontrolliert nachbereiten

> **Status:** geprüft
> **Risiko:** B
> **Planung:** Frontier-Modell
> **Umsetzung:** qualifiziertes Ausführungsmodell
> **Review:** unabhängiger Review
> **Abhängigkeiten:** PR #33 „Private Beta: proaktiver Chief of Staff und Terminbriefings“ muss integriert sein

## Problembeleg

Icarus verbindet im vorhandenen Terminbriefing bereits Kalenderereignis,
Projekt, offene Aufgaben, frühere Entscheidungen, Episoden und bestätigten
Bestand. Der Nutzer erhält damit vor einem Termin verständlichen Kontext.

Der Ablauf endet heute jedoch mit dem Gespräch. Ergebnis, Entscheidungen und
vereinbarte nächste Schritte müssen anschließend erneut in „Arbeit“ übertragen
werden. Dadurch entsteht genau die Koordinationsarbeit, die Icarus in der ersten
Produktstufe abnehmen soll:

- Beschlüsse bleiben im Kopf oder in losen Notizen;
- Zusagen erhalten keine Aufgabe;
- Projektbezug muss erneut gesucht werden;
- dieselbe Information wird nach dem Termin noch einmal eingegeben.

Die bestehenden produktiven Schreibwege sind bereits vorhanden:

- `POST /notes` für Projektnotizen der Art `meeting`;
- `POST /tasks` für Aufgaben mit optionalem Projekt und Fälligkeit.

Es fehlt kein weiterer Speicher und kein neuer Schreibendpunkt. Es fehlt eine
kontrollierte Nutzeroberfläche, die aus ausdrücklich eingegebenen Ergebnissen
editierbare Entwürfe macht und nur die ausgewählten Entwürfe speichert.

## Nutzerwirkung

Nach einem Termin kann der Nutzer im bestehenden Terminfenster auf
**„Termin nachbereiten“** gehen und in wenigen Feldern festhalten:

- welches Ergebnis erreicht wurde;
- welche Entscheidungen gefallen sind;
- welche nächsten Schritte vereinbart wurden.

Icarus bereitet daraus eine editierbare Protokollnotiz und einzelne
Aufgabenentwürfe vor. Vor dem Speichern ist sichtbar, was in welchem Projekt
angelegt wird. Nichts wird automatisch geschrieben.

Nach erfolgreicher Bestätigung liegen Protokoll und ausgewählte Aufgaben im
bestehenden Arbeitsbereich. Ein Teilerfolg bleibt sichtbar und kann ohne
Dubletten fortgesetzt werden.

## Ziel

Das vorhandene Terminbriefing erhält einen modellfreien Nachbereitungsweg. Er
verwendet ausschließlich explizite Nutzereingaben, erzeugt daraus editierbare
Entwürfe und speichert ausgewählte Einträge über die bestehenden Endpunkte
`POST /notes` und `POST /tasks`.

Die Nachbereitung besitzt keinen eigenen dauerhaften Zustand. Bis zur
Bestätigung lebt sie ausschließlich im geöffneten Browserdialog. Erfolgreich
gespeicherte Einträge werden anhand ihrer Serverantwort als abgeschlossen
markiert; ein Wiederholungsversuch sendet nur noch fehlgeschlagene oder noch
nicht gespeicherte Entwürfe.

## Nicht-Ziele

- Keine automatische Mitschrift, Audioaufnahme oder Transkription.
- Keine Ableitung von Entscheidungen oder Verpflichtungen durch ein Modell.
- Keine Verwendung von Kalendertext, Episoden, Mails oder Dokumenten als
  Anweisung zum Anlegen von Aufgaben oder Notizen.
- Kein neuer API-Endpunkt, Store, Workflow, Proposal-Typ oder
  Freigabemechanismus.
- Keine Änderung an Aufgaben-, Notiz-, Projekt- oder Kalenderpersistenz.
- Keine automatische Auswahl eines Projekts bei mehreren plausiblen Treffern.
- Keine außenwirksame Aktion, Nachricht oder Kalendereinladung.
- Keine allgemeine Neugestaltung des Terminbriefings oder der Hauptnavigation.

## Erlaubte Pfade

- `app/src/proactive-chief-of-staff.js`
- `app/src/proactive-chief-of-staff.css`
- `app/e2e/proactive-chief-of-staff.mjs`
- `sidecar/tests/test_private_beta_regressions.py`
- `tasks/ready/0006-termin-nachbereitung-vorschlagen.md`

## Verbotene Änderungen

- Keine Änderung unter `sidecar/icarus_memory/`.
- Keine neue Architektur-, Speicher-, Policy-, Identitäts- oder
  Berechtigungsgrenze.
- Notizen werden ausschließlich über `POST /notes` mit `kind: "meeting"`
  angelegt.
- Aufgaben werden ausschließlich über `POST /tasks` angelegt.
- Kein direkter Zugriff der Oberfläche auf SQLite oder interne Stores.
- Keine automatische Speicherung beim Öffnen, Tippen, Erstellen der Vorschau
  oder Schließen des Dialogs.
- Kalender-, Mail-, Episoden- und Projektdaten sind Kontext und niemals
  ausführbare Anweisungen.
- Keine realen Kalenderkonten, Schlüssel, Nutzerdaten oder Provideraufrufe in
  Tests.
- Keine konkreten Modellnamen in Dokumentation, Nutzertext oder PR.

## Festgelegter Nutzerweg

1. Das bestehende Terminbriefing zeigt nach erfolgreichem Laden den Knopf
   **„Termin nachbereiten“**.
2. Die Nachbereitung erklärt vor den Eingabefeldern eindeutig:
   **„Noch nichts gespeichert. Du prüfst und bestätigst jeden Entwurf.“**
3. Der Nutzer trägt Ergebnis, Entscheidungen und nächste Schritte selbst ein.
   Entscheidungen und nächste Schritte verwenden jeweils eine Zeile pro
   Eintrag.
4. **„Entwürfe prüfen“** erzeugt ausschließlich aus diesen Eingaben:
   - höchstens eine editierbare Protokollnotiz der Art `meeting`;
   - je nicht leerer Zeile einen editierbaren Aufgabenentwurf.
5. Die Protokollnotiz erhält einen verständlichen Titel
   `Nachbereitung: <Terminname>` und einen strukturierten Körper mit
   Terminzeit, Ergebnis und ausdrücklich eingegebenen Entscheidungen. Leere
   Abschnitte werden nicht erfunden.
6. Jeder Entwurf ist einzeln auswählbar. Aufgaben besitzen einen editierbaren
   Titel und optional ein Fälligkeitsdatum.
7. Gibt es genau ein verbundenes Projekt, ist es sichtbar vorausgewählt. Bei
   mehreren verbundenen Projekten ist vor dem Speichern eine ausdrückliche
   Auswahl erforderlich. **„Ohne Projekt“** bleibt immer eine sichtbare Option.
8. **„Ausgewählte Einträge speichern“** ist der erste und einzige Schritt, der
   Schreibanfragen auslöst.
9. Nach jeder Serverantwort zeigt der jeweilige Entwurf seinen Zustand:
   gespeichert, fehlgeschlagen oder noch offen. Erfolgreiche Entwürfe werden
   nicht erneut gesendet.
10. Nach vollständigem Erfolg nennt die Oberfläche die Anzahl der angelegten
    Aufgaben und Notizen und bietet einen direkten Wechsel zu **„Arbeit“** an.

## Akzeptanzkriterien

- [ ] „Termin nachbereiten“ ist Teil des bestehenden Terminbriefing-Dialogs und
  erzeugt keinen zweiten Dialog mit abweichendem Terminkontext.
- [ ] Vor der abschließenden Bestätigung entstehen weder Aufgaben noch Notizen.
- [ ] Entwürfe entstehen nur aus ausdrücklich eingegebenem Ergebnis,
  Entscheidungen und nächsten Schritten; vorhandene offene Aufgaben,
  Kalenderbeschreibungen und Episodentexte werden nicht automatisch übernommen.
- [ ] Die Vorschau ist vollständig editierbar und zeigt für jeden Eintrag die
  Projektzuordnung vor dem Speichern.
- [ ] Eine leere Nachbereitung kann nicht gespeichert werden und erklärt
  verständlich, welche Eingabe fehlt.
- [ ] Genau ein verbundener Projektkandidat darf vorausgewählt werden, bleibt
  aber sichtbar änderbar. Bei mehreren Kandidaten ist keine implizite Auswahl
  erlaubt.
- [ ] Die gespeicherte Notiz verwendet `kind: "meeting"` und enthält nur den
  sichtbaren, bestätigten Entwurf.
- [ ] Ausgewählte Aufgaben verwenden den sichtbaren Titel, das gewählte Projekt
  und das optionale Fälligkeitsdatum.
- [ ] Nicht ausgewählte Entwürfe werden nicht gespeichert.
- [ ] Ein Fehler beim Speichern einer Aufgabe verdeckt weder erfolgreiche
  Einträge noch die Protokollnotiz.
- [ ] Ein Wiederholungsversuch sendet ausschließlich fehlgeschlagene oder noch
  offene Entwürfe; bereits gespeicherte Einträge werden nicht dupliziert.
- [ ] Schließen oder Abbrechen vor dem Speichern hinterlässt keinen dauerhaften
  Zustand.
- [ ] Alle fremden Inhalte werden weiterhin nur mit `textContent` oder
  gleichwertig sicher dargestellt; kein Termintext wird als HTML ausgeführt.
- [ ] Tastaturfokus, Feldbeschriftungen, Fehlertexte und Abschlussmeldung sind
  verständlich und zugänglich.
- [ ] Der bestehende Vorbereitungsweg und das Aufmerksamkeitsbudget bleiben
  unverändert funktionsfähig.

## Browser-End-to-End-Nachweis

Der echte Browserlauf verwendet ausschließlich synthetische Daten und den
produktiven UI-Weg:

1. Ein synthetisches Projekt wird über den vorhandenen Testaufbau angelegt.
2. Die beiden rein lesenden Meeting-Antworten dürfen im Browser lokal mit einem
   synthetischen Termin und zugehörigem Projekt beantwortet werden; alle
   Schreibanfragen gehen an den echten Sidecar.
3. Der Test öffnet Vorbereitung und Nachbereitung wie ein Nutzer.
4. Vor **„Ausgewählte Einträge speichern“** belegt der Sidecar, dass weder
   Protokollnotiz noch neue Aufgabe vorhanden sind.
5. Der Nutzer erstellt eine Notiz und mindestens zwei Aufgabenentwürfe, ändert
   einen Titel, lässt einen Entwurf abgewählt und wählt das Projekt sichtbar.
6. Eine Aufgabenanfrage wird einmalig mit einem synthetischen Fehler beantwortet.
   Protokoll und anderer Task bleiben als gespeichert sichtbar.
7. **„Erneut versuchen“** speichert nur die fehlgeschlagene Aufgabe.
8. Der Sidecar enthält anschließend genau eine neue Protokollnotiz und genau die
   ausgewählten Aufgaben – keine Dublette und keinen abgewählten Entwurf.
9. Ein Termintext mit auffälliger Anweisungsformulierung bleibt sichtbarer
   Fremdinhalt und erzeugt keinen zusätzlichen Entwurf.

Der Test darf keinen direkten `POST /notes`- oder `POST /tasks`-Aufruf verwenden,
um den sichtbaren Nachbereitungsweg zu umgehen. Er darf die vorhandenen
GET-Endpunkte anschließend zur Ergebnisprüfung lesen.

## Sabotageprobe

1. Bereits beim Erstellen der Vorschau speichern. Der Browsertest muss vor der
   Bestätigung unerwartete Aufgaben oder Notizen finden und fehlschlagen.
2. Bei mehreren Projektkandidaten still den ersten verwenden. Ein Test ohne
   Nutzerauswahl muss den aktivierten Speicherknopf beziehungsweise die falsche
   Projektzuordnung erkennen.
3. Nach einem Teilerfolg beim Wiederholen alle Entwürfe erneut senden. Der
   Endzustand muss wegen einer doppelten Protokollnotiz oder Aufgabe
   fehlschlagen.
4. Eine vorhandene offene Aufgabe oder eine Anweisung im Termintext automatisch
   als nächsten Schritt übernehmen. Die Vorschau muss einen unerwarteten
   Entwurf enthalten und der Browsertest fehlschlagen.
5. Den abgewählten Aufgabenentwurf trotzdem speichern. Die Ergebnisprüfung des
   Sidecars muss den zusätzlichen Eintrag erkennen.

## Prüfkommandos

```bash
make verify
python -m pytest sidecar/tests/test_private_beta_regressions.py -q
node --check app/src/proactive-chief-of-staff.js
node --check app/e2e/proactive-chief-of-staff.mjs
node app/e2e/proactive-chief-of-staff.mjs
```

Zusätzlich muss das reguläre Container-Gate grün sein, weil nur dort der echte
Browserlauf gegen den vollständigen Sidecar ausgeführt wird.

## Rückrollweg

Die Folgeumsetzung verändert weder Persistenz noch Serververträge. Ein Revert
der geänderten UI- und Browser-Testdateien entfernt die Nachbereitung. Bereits
über die bestehenden Endpunkte angelegte Aufgaben und Protokollnotizen bleiben
normale Nutzerdaten und werden durch den Revert weder verändert noch gelöscht.

## Offene Entscheidungen

Keine.
