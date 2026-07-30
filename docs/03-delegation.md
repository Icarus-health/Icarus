# Säule 4: Kontrollierte Delegation und Ausführung

> Status: **Spezifikation, nicht implementiert.** Dieses Dokument legt fest, was gebaut werden muss, bevor Icarus irgendetwas ausführen darf.

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

Zwei Punkte, die in der Praxis den Unterschied machen:

**Eine Freigabe gilt einmal.** Nicht für die Sitzung, nicht für „ähnliche Fälle". Wer Dauerfreigaben will, definiert eine Regel — explizit, benannt und widerrufbar — statt sie sich implizit durch Klicken zu erschleichen.

**Trockenlauf vor jeder A-Aktion.** Der genaue Inhalt, der genaue Empfänger, die genaue Änderung. Nicht „Mail an Team senden?", sondern der fertige Text mit Adressliste. Der häufigste reale Schaden ist nicht die böswillige Aktion, sondern die plausibel klingende an den falschen Adressaten.

## Audit-Log

Nicht optional, und nicht dasselbe wie Logging.

Jede ausgeführte Aktion wird festgehalten mit: Zeitpunkt, auslösender Anfrage, Klasse, Freigabestufe, wer freigegeben hat, verwendeten Modell, tatsächlichen Parametern, Ergebnis. Das Log ist **anhängend** — Einträge werden nicht geändert und nicht gelöscht.

Der Grund ist derselbe wie bei Säule 1: Ein System, dem man über Jahre vertrauen soll, muss im Nachhinein erklären können, was es getan hat und warum. Ohne das ist jede Fehlersuche Spekulation.

## Umgang mit dem Selbstmodell

Zwei Verbindungen zu [Säule 1](02-selbstmodell.md), die die Policy-Schicht durchsetzen muss:

**`constraint` ist bindend.** Aussagen mit `kind: constraint` sind harte Grenzen. Bei Konflikt mit einer Anweisung gewinnt der Constraint, und das System sagt, warum.

**`sensitivity` steuert den Abfluss.** Aussagen mit `special_category` — Gesundheit, Weltanschauung, alles besonders Geschützte — dürfen nicht ungefragt an externe Modelle gehen. Im Schema ist das heute nur eine Markierung; die Durchsetzung gehört hierher. Solange sie fehlt, ist der Schutz nominell.

## Secrets

Zugangsdaten für Mail, Kalender und Konnektoren gehören nicht in Prompts und nicht ins Gedächtnis. Sie liegen außerhalb des Modellkontexts, und Werkzeuge bekommen sie zur Laufzeit — nicht als Text, den ein Modell sieht und im schlechtesten Fall wiedergibt.

Im Skelett heißt das vorerst nur: `.env` ist in `.gitignore`, und `AUTH_DISABLED` bleibt `false`. Das ist das Minimum, nicht die Lösung.

## Erst danach: Computer-Use

Wenn die Schicht steht, ist Agent Zero der stärkste offene Kandidat: echter Linux-Desktop in einer beobachtbaren Umgebung, Browser mit DOM-Annotation, Dokumentenarbeit, Multi-Agenten. Open Interpreter und Open WebUIs eigene Computer-Erweiterung sind die Alternativen.

Die Anbindung erfolgt dann **hinter** der Policy-Schicht, nie direkt an der Oberfläche. Praktisch: Der Computer-Use-Container bekommt keinen eigenen Zugang zu Konnektoren und Secrets, sondern stellt Anträge, die dieselbe Klassifikation durchlaufen wie alles andere.
