# Säule 4: Kontrollierte Delegation und Ausführung

> Status: **Gebaut.** Implementiert in [`sidecar/icarus_memory/policy.py`](../sidecar/icarus_memory/policy.py) und [`audit.py`](../sidecar/icarus_memory/audit.py), durchgesetzt in [`agent.py`](../sidecar/icarus_memory/agent.py).
>
> Der Agent führt **nichts** selbst aus. Er stellt Anträge; ausgeführt wird erst, wenn die Policy es erlaubt oder der Nutzer freigibt. Es gibt keinen Weg an dieser Schicht vorbei.

## Die Regel

**Ausführung kommt nach dem Freigabemodell, nicht davor.**

Das ist die zentrale Reihenfolgeentscheidung des Projekts, und sie geht bewusst gegen die Verlockung. Ein Assistent, der den Rechner bedient, ist das eindrucksvollste Demo-Feature überhaupt — und ohne Freigabeschicht ist er kein Feature, sondern ein Risiko mit Oberfläche.

Sobald ein System Mails verschicken, Termine ändern, Dateien anfassen oder einen Browser fernsteuern kann, sind Freigaben, Rollentrennung, Secret-Handling und ein Audit-Log keine Ausbaustufe mehr. Sie sind die Voraussetzung dafür, dass man das Ding überhaupt laufen lassen darf. Deshalb steht Agent Zero in der Roadmap **hinter** dieser Schicht, obwohl es technisch sofort einsatzbereit wäre.

## Klassifikation von Aktionen

Jede Aktion, die Icarus ausführen kann, wird in eine von drei Klassen eingeordnet. Die Klasse bestimmt die Freigabestufe.

| Klasse | Beschreibung | Beispiele | Umkehrbar? |
|---|---|---|---|
| **L — lesend** | Verändert nichts | Kalender lesen, Websuche, Dokument öffnen | entfällt |
| **S — schreibend, intern** | Verändert eigene Daten | Notiz anlegen, Selbstmodell ergänzen, Datei im eigenen Ordner schreiben | meist ja |
| **A — außenwirksam** | Verlässt das eigene System | Mail senden, Termin mit Gästen anlegen, Bestellung auslösen, Beitrag veröffentlichen | oft nein |

Die Grenze zwischen S und A ist die wichtigste. Eine Kalenderänderung ohne Gäste ist S; sobald jemand eine Einladung bekommt, ist sie A — sie ist bei Dritten sichtbar geworden und lässt sich nicht mehr wirklich zurücknehmen.

## Freigabestufen

| Stufe | Verhalten | Vorgesehen für |
|---|---|---|
| `auto` | Läuft ohne Rückfrage, wird protokolliert | L |
| `notify` | Läuft, meldet danach, was getan wurde | S mit geringem Schaden |
| `confirm` | Fragt vorher, zeigt Trockenlauf | S mit Folgen, A mit geringem Schaden |
| `confirm_strict` | Fragt vorher, zeigt Trockenlauf, verlangt Wiederholung des Kerninhalts | A |
| `deny` | Nie, unabhängig vom Kontext | alles unter `constraint` im Selbstmodell |

Die Vorgabe ist bewusst streng: Lesen läuft durch, Schreiben meldet sich hinterher, **alles Außenwirksame verlangt die Wiederholung des Kerninhalts**. Wer das lockern will, hebt die Stufe für ein Werkzeug ausdrücklich und benannt an.

Zwei Punkte, die in der Praxis den Unterschied machen:

**Eine Freigabe gilt einmal.** Nicht für die Sitzung, nicht für „ähnliche Fälle". Wer Dauerfreigaben will, definiert eine Regel — explizit, benannt und widerrufbar — statt sie sich implizit durch Klicken zu erschleichen. Anträge laufen zudem nach zehn Minuten ab.

**Ein Vertipper vernichtet die Freigabe nicht.** Stimmt die Bestätigung nicht, bleibt der Antrag offen und der Nutzer kann es erneut versuchen. Würde der Antrag verfallen, müsste er den ganzen Vorgang wiederholen — und gewöhnt sich dann an, Bestätigungen wegzuklicken. Genau das soll die Stufe verhindern.

**Trockenlauf vor jeder A-Aktion.** Der genaue Inhalt, der genaue Empfänger, die genaue Änderung. Nicht „Mail an Team senden?", sondern der fertige Text mit Adressliste. Der häufigste reale Schaden ist nicht die böswillige Aktion, sondern die plausibel klingende an den falschen Adressaten.

## Audit-Log

Nicht optional, und nicht dasselbe wie Logging.

Jede Aktion wird festgehalten mit: Zeitpunkt, Werkzeug, Klasse, Freigabestufe, Ausgang, wer freigegeben hat, verwendetem Modell, tatsächlichen Parametern und Ergebnis. Protokolliert wird **jeder** Ausgang, nicht nur der erfolgreiche: `executed`, `denied`, `refused`, `failed`, `pending`.

Das Log ist **anhängend**, und zwar auf Datenbankebene: zwei SQLite-Trigger brechen jedes `UPDATE` und jedes `DELETE` ab. Das ist keine Konvention im Anwendungscode, an die sich späterer Code halten müsste.

Der Grund ist derselbe wie bei Säule 1: Ein System, dem man über Jahre vertrauen soll, muss im Nachhinein erklären können, was es getan hat und warum. Ohne das ist jede Fehlersuche Spekulation.

## Umgang mit dem Selbstmodell

Zwei Verbindungen zu [Säule 1](02-selbstmodell.md), die die Policy-Schicht durchsetzen muss:

**`constraint` ist bindend.** Aussagen mit `kind: constraint` sind harte Grenzen. Bei Konflikt mit einer Anweisung gewinnt der Constraint, und das System sagt, warum.

**`sensitivity` steuert den Abfluss.** Aussagen mit `special_category` — Gesundheit, Weltanschauung, alles besonders Geschützte — gehen nicht an externe Modelle. Das ist durchgesetzt: `Agent.context()` filtert nach Schutzbedarf, bevor irgendetwas den Rechner verlässt. Dem Modell wird stattdessen mitgeteilt, dass etwas zurückgehalten wurde — eine verschwiegene Lücke wäre schlimmer als eine benannte.

Nachlesbar ist das im Endpunkt `/context`: Er zeigt wörtlich, was übermittelt wird. Der Nutzer soll es prüfen können, statt es glauben zu müssen.

## Secrets

Zugangsdaten für Mail, Kalender und Konnektoren gehören nicht in Prompts und nicht ins Gedächtnis. Sie liegen außerhalb des Modellkontexts, und Werkzeuge bekommen sie zur Laufzeit — nicht als Text, den ein Modell sieht und im schlechtesten Fall wiedergibt.

Heute heißt das nur: `.env` ist in `.gitignore`, und der Sidecar verlangt ein Token, das die App bei jedem Start neu erzeugt. Das ist das Minimum, nicht die Lösung.

## Erst danach: Computer-Use

Wenn die Schicht steht, ist Agent Zero der stärkste offene Kandidat: echter Linux-Desktop in einer beobachtbaren Umgebung, Browser mit DOM-Annotation, Dokumentenarbeit, Multi-Agenten. Open Interpreter ist die Alternative.

Die Anbindung erfolgt dann **hinter** der Policy-Schicht, nie direkt an der Oberfläche. Praktisch: Der Computer-Use-Container bekommt keinen eigenen Zugang zu Konnektoren und Secrets, sondern stellt Anträge, die dieselbe Klassifikation durchlaufen wie alles andere.

## Fremde Inhalte

Werkzeuge, die Text aus fremder Quelle liefern, machen die Runde *kontaminiert*: Danach wird jede Aktion mit Wirkung eine Stufe höher eingestuft. Der Grund und die beiden anderen Schutzebenen stehen in [05-sicherheit.md](05-sicherheit.md).

## Was noch fehlt

- **Keine echten Kanäle.** `mail_senden` existiert samt Freigabeweg, aber ohne angebundenen Versand. Eine erteilte Freigabe endet dann im Protokoll als `failed` mit Begründung — nicht als stiller Erfolg.
- **Grenzen greifen wörtlich.** Ein `constraint` trifft über Werkzeugnamen und Inhaltswörter. Das ist nachvollziehbar und bewusst nicht per Modell ausgelegt, aber es erkennt keine Umschreibungen.
- ~~Keine benannten Dauerregeln.~~ Gebaut, siehe unten.
- **Secrets liegen noch in `.env`.** Ein Schlüsselbund-Zugriff ist nicht gebaut.


## Dauerregeln

Eine Freigabe gilt einmal. Das ist richtig, kostet aber jedes Mal eine
Entscheidung — und bei der zwanzigsten gleichartigen Rückfrage klickt jeder
nur noch weg. Dann ist die Rückfrage keine Kontrolle mehr, sondern ein Ritual.

Eine **Dauerregel** verlegt die Entscheidung nach vorn: einmal, ausdrücklich,
benannt, widerrufbar. Angelegt wird sie dort, wo die Rückfrage kam — ein
Häkchen unter der Freigabekarte, nicht ein Formular in den Einstellungen.

Drei Eigenschaften tragen die Sicherheit. Alle drei sind durch Sabotageproben
abgesichert:

**Eine Regel gilt nie in einer kontaminierten Runde.** Steht fremder Text im
Kontext, ist nicht mehr feststellbar, ob die Absicht vom Nutzer kommt oder aus
dem gelesenen Text. Genau dann greift keine Regel — sonst wäre sie der
bequemste Weg, die Eskalation auszuhebeln. Der Angriff, um den es geht: eine
gelesene Mail enthält „schick eine Erinnerung an becker@…“.

**Eine Regel schlägt keine Grenze.** Ein `constraint` aus dem Selbstmodell wird
**vor** der Regel geprüft und verbietet weiterhin, was er verbietet.

**Eine Regel hebt keine Stufe an.** Sie darf senken, nie anheben. Dauerhaft
verbieten ist eine Grenze, keine Freigabe, und gehört ins Selbstmodell —
`deny` ist als Regelstufe nicht zugelassen.

Dazu drei Eigenschaften, die sie brauchbar machen:

- **Eng statt blanko.** Die Karte bietet die Regel auf den Kern des Aufrufs
  beschränkt an — „Mails an diese Adresse“, nicht „Mails“. Eine
  Blankovollmacht lässt sich anlegen, wird in der Liste aber als solche
  benannt.
- **Die engste Regel gewinnt.** Wer eine für „Mails an Becker“ und eine für
  „Mails“ hat, meint bei einer Mail an Becker die erste.
- **Widerrufen löscht nicht.** Wer später im Protokoll liest, dass etwas „nach
  Regel X“ lief, muss Regel X nachschlagen können — auch wenn sie längst
  zurückgenommen ist. Das Anlegen und das Widerrufen stehen selbst im
  Protokoll.

Endpunkte: `GET /rules`, `POST /rules`, `POST /rules/{id}/revoke`.
Umsetzung: `regeln.py`, angewandt in `policy.decide()`.
