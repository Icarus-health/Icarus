# Mail im Gesprächsfenster

Stand 2026-08-01. Verbindlich für die `/mail`-Endpunkte in `server.py` und den
Posteingang in `app/src/main.js`.

Mail dort, wo man ohnehin schreibt: lesen, antworten, ins Gedächtnis nehmen —
ohne die Anwendung zu wechseln.

## Die eine Regel, die hier alles bestimmt

> **Jeder kann dir eine Mail schreiben.**

Das ist der gefährlichste Eingabekanal, den es gibt, und der einzige, bei dem
ein Fremder den Zeitpunkt bestimmt. Alles Weitere folgt daraus.

| Passiert | Passiert nie |
| --- | --- |
| Nachrichten lesen und anzeigen | Aus einer Mail wird eine Anweisung |
| Eine Antwort verfassen | Eine Antwort geht ohne Freigabe hinaus |
| Eine Mail als Episode aufnehmen | Aus einer Mail wird ein Fakt im Bestand |
| Den Wortlaut als fremd rahmen | Der Posteingang läuft von selbst ins Gedächtnis |

## Der Sendeknopf ist keine Abkürzung

Er ruft `POST /tools/mail_senden` — **dasselbe Werkzeug wie das Modell**. Damit
geht er durch Policy, Freigabe mit vollem Trockenlauf und Protokoll.

```
E-Mail senden
An:      Dr. Meier <meier@example.com>
Betreff: Re: Jour fixe Dienstag
Antwort auf: <abc123@example.com>
---
Zehn Uhr passt.
```

Der Knopf ist kein zweiter Weg an der Kontrolle vorbei, sondern derselbe Weg,
kürzer: Antwort tippen, senden, und die Freigabe steht direkt darüber.

**Die Bestätigung bleibt streng.** Außenwirksames verlangt, die Empfängeradresse
zu wiederholen. Bei einer Antwort wiegt das schwerer als sonst: Die Adresse
kommt aus `Reply-To`, und den setzt der Absender. Eine Mail, die aussieht, als
käme sie von Dr. Meier, kann `Reply-To: angreifer@example.com` tragen — die
Wiederholung ist genau die Stelle, an der das auffällt.

## Antworten hängen am Verlauf

`In-Reply-To` und `References` gehen mit. Ohne sie erscheint die Antwort beim
Empfänger als neue Nachricht statt im Verlauf, und wer zwanzig Mails am Tag
bekommt, findet sie dann nicht wieder.

Der Betreff bekommt `Re:` — aber nicht `Re: Re: Re:`.

## Ins Gedächtnis heißt Rohmaterial, nicht Wissen

`POST /mail/{uid}/remember` legt eine **Episode** an: `kind=message`,
`source_type=email`, `source_ref` die `Message-ID`, `occurred_at` der Zeitpunkt
der Mail. Der Bestand bleibt unberührt.

Der Unterschied ist der ganze Punkt und der Grund, warum der Knopf trotz der
Herkunft unbedenklich ist. Eine Episode hält fest, **dass etwas vorlag**; sie
behauptet nichts über die Person. Ob daraus eine dauerhafte Aussage folgt,
entscheidet die Verdichtung ([`10`](10-verdichtung.md)) — und die legt vor,
statt zu schreiben.

Eine Mail mit „IGNORIERE ALLE VORHERIGEN ANWEISUNGEN“ darf deshalb aufgenommen
werden: Sie ist eine Tatsache über den Absender. Was nicht passieren darf, ist
dass daraus ein Fakt oder eine Handlung wird — und beides prüft
`test_posteingang.py` ausdrücklich.

**Auf Zuruf, nicht von selbst.** Ein Posteingang, der vollständig in die
Episoden liefe, brächte Werbung und Newsletter mit, und jedes Stück davon ginge
später als Material ins Modell. Der Zeitplan ([`11`](11-zeitplan.md)) rührt Mail
deshalb nicht an.

## Der Wortlaut ist sichtbar fremd

Der geöffnete Text steht monospaced im selben Rahmen wie ein Trockenlauf. Er
darf nie so aussehen, als hätte Icarus ihn geschrieben — die Verwechslung von
Beleg und Behauptung ist der Fehler, gegen den das ganze Projekt gebaut ist.

## Was die Liste nicht trägt

`GET /mail` liefert Absender, Betreff, Datum und Vorschau — **keinen
Volltext**. Zwanzig ganze Mails sind ein Vielfaches der Datenmenge, und sie
stehen ohnehin zusammengefaltet da. Der volle Text kommt erst beim Öffnen über
`GET /mail/{uid}`.

Abgeschnitten wird im **Endpunkt**, nicht im Konnektor: So hängt die Zusage
daran, was ausgeliefert wird, statt daran, dass `inbox()` den Text zufällig
nicht füllt.

**Nichts wird als gelesen markiert.** IMAP wird durchgehend mit `readonly=True`
geöffnet. Wer seine Mail woanders bearbeitet, soll dort denselben Zustand
vorfinden — Icarus schaut zu, es räumt nicht auf.

## Zwei Fehler, die beim Prüfen auffielen

Beide von derselben Art: Der Code las die **Einstellungsdatei**, wo die
**Umgebung** die Wahrheit ist.

**Der Einrichtungsassistent löschte den Mailzugang.** `PUT /setup` räumte alle
bekannten Umgebungsvariablen weg, bevor es die Einstellungen neu anwandte. Wer
`ICARUS_IMAP_HOST=…` vor den Start setzte, verlor seinen Posteingang beim ersten
Speichern — still, ohne Meldung, und der Assistent speichert beim Überspringen.
Jetzt werden nur die Namen geräumt, die beim Start **aus der Datei** kamen;
`apply_to_env` meldet sie ohnehin zurück.

**Das Antwortfeld war grau, obwohl SMTP eingerichtet war.** `can_send` las
`settings.mail.smtp_host` statt der Umgebung — und daraus liest
`MailConfig.from_env`, also die tatsächlich wirksame Konfiguration.

## Schnittstelle

| Weg | Was |
| --- | --- |
| `GET /mail` | Liste ohne Volltext, Anzahl ungelesen, ob Senden geht |
| `GET /mail/{uid}` | Eine Nachricht mit vollem Text, `Message-ID`, Antwortadresse |
| `POST /mail/{uid}/remember` | Als Episode aufnehmen; `409` ohne Mailkonto |
| `POST /tools/mail_senden` | Erzeugt eine Freigabe, sendet nicht |

Ohne eingerichteten Mailzugang antworten alle mit `409` und einem Satz, der
sagt, was zu tun ist — statt eines nackten Fehlers.

## Was offen ist

**Kein Verfassen ohne Bezug.** Antworten geht, eine neue Mail von Hand
schreiben noch nicht. Über das Gespräch geht es (`mail_senden` als Werkzeug),
über den Posteingang nicht.

**Keine Anhänge.** Weder lesen noch senden. Anhänge sind der zweite gefährliche
Kanal nach dem Text selbst, und sie gehören eigens bedacht.

**Nur `text/plain`.** Eine reine HTML-Mail zeigt einen leeren Text. Das ist
ehrlicher als ein halbherziger Umbau, aber es fehlt.

**Kein Ordnerwechsel.** Nur INBOX.

## Verwandte Dokumente

- [`03-delegation.md`](03-delegation.md) — Aktionsklassen und Freigaben
- [`05-sicherheit.md`](05-sicherheit.md) — warum fremder Text eingerahmt wird
- [`08-gedaechtnisschichten.md`](08-gedaechtnisschichten.md) — was eine Episode ist
- [`13-nutzerfreundlichkeit.md`](13-nutzerfreundlichkeit.md) — wie der Zugang eingerichtet wird
