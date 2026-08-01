# Nutzerfreundlichkeit

Stand 2026-08-01. Der Grundsatz steht in [`../CLAUDE.md`](../CLAUDE.md) und gilt
für jede Änderung an der Oberfläche.

> **Immer die für den Nutzer einfachste Lösung.**

Nicht die einfachste zu bauende. Nicht die technisch sauberste. Die, bei der der
Mensch vor dem Bildschirm am wenigsten tun, wissen und entscheiden muss.

Das klingt nach Geschmack und ist es nicht. Die Fragen, an denen sich das
entscheidet, haben richtige Antworten.

## 1. Muss der Nutzer etwas wissen, das er nicht wissen kann?

**Vorher.** Der Mailblock hatte sieben Felder, davon vier für Serverangaben:
IMAP-Server, IMAP-Port, SMTP-Server, SMTP-Port. Wer den IMAP-Host seines
Anbieters nicht kennt, weiß auch nicht, wonach er suchen soll.

**Jetzt.** Adresse eintragen. Icarus erkennt den Anbieter an der Endung und
trägt Hosts und Ports selbst ein. Die Serverangaben stehen darunter eingeklappt
— für eigene Domains, also genau die Gruppe, die sie auch kennt.

Die Tabelle steht in `providers_mail.py` und ist eine **Bequemlichkeit, kein
Zwang**: „Anderer Anbieter“ bleibt wählbar, das Protokoll bleibt IMAP und SMTP.
Ändert ein Anbieter seinen Hostnamen, ist eine Zeile falsch und überschreibbar —
ein anderer Schadensfall als eine Anbieter-API, die abgekündigt wird.

**Und der Hinweis, der den meisten Ärger spart.** Gmail, iCloud, Outlook, Yahoo
und Fastmail lehnen das Kontokennwort ab und verlangen ein App-Passwort. Wer das
nicht weiß, tippt dreimal sein richtiges Kennwort ein, bekommt dreimal
„Anmeldung fehlgeschlagen“ und hält das Programm für kaputt. Der Hinweis steht
samt Link **vor** dem ersten Prüfen.

## 2. Muss er dasselbe zweimal eingeben?

Ein Ordner wurde bis eben **dreimal** eingetragen: einmal in der Freigabe,
einmal beim Aufnehmen, einmal im Zeitplan. Jedes Mal als getippter Pfad, jedes
Mal mit derselben Chance auf einen Tippfehler.

| Stelle | Vorher | Jetzt |
| --- | --- | --- |
| Freigabe | Feld, Pfade durch `:` getrennt | Liste, ein Ordner je Zeile |
| Aufnehmen | freies Textfeld | Auswahl aus dem Freigegebenen |
| Zeitplan | Feld, Pfade durch `:` getrennt | Kästchen je freigegebenem Ordner |
| Kalender | eigene CalDAV-Adresse | aus dem Mailanbieter abgeleitet |

Getippt wird noch genau einmal: beim Freigeben. Danach nie wieder.

Beim Kalender ist die ehrliche Auskunft der eigentliche Gewinn. Google und
Microsoft haben die einfache Anmeldung an CalDAV abgeschaltet — dort steht
jetzt:

> Google lässt CalDAV nicht mehr mit App-Passwort zu. Der Kalender bleibt hier
> außen vor — die Mail funktioniert.

Das zu verschweigen hieße, den Nutzer eine Viertelstunde suchen zu lassen, bevor
er aufgibt und annimmt, das Programm könne es nicht. Es kann es; der Anbieter
lässt es nicht zu, und genau das gehört dort zu stehen.

## 3. Muss er tippen, was er zeigen könnte?

Ganz vermeiden lässt sich das Tippen nicht. Im Container gibt es keinen nativen
Auswahldialog, und in der App wäre er eine zusätzliche Abhängigkeit — für einen
Gewinn, den die Auswahllisten aus Punkt 2 größtenteils schon bringen.

Was sich vermeiden lässt, ist der **stille Tippfehler**. Ein Ordner wird beim
Hinzufügen geprüft:

```
/Users/du/Dokumente/Vaultt   → „Diesen Ordner gibt es nicht.“
/Users/du/Dokumente/notiz.md → „Das ist eine Datei, kein Ordner.“
/Users/du/Dokumente/Vault    → „1.243 lesbare Dateien gefunden.“
```

Die letzte Zeile ist die wichtigste: Sie bestätigt, dass es der **gemeinte**
Ordner ist. Ein Pfad, der existiert und leer ist, ist meistens der falsche.

Ohne diese Prüfung merkt man den Tippfehler erst, wenn die Aufnahme scheitert —
drei Bildschirme später, mit einer Meldung, die ihn nicht nennt.

**Die Prüfung zählt nur, sie liest nicht.** Sie läuft ja *vor* der Freigabe;
würde sie Inhalte zurückgeben, wäre sie ein Weg an der Pfadgrenze vorbei.

## 4. Sieht er, was passiert ist?

Ein Knopf, der drückt und nichts sagt, ist schlimmer als keiner.

Zwei Fälle sind hier real aufgetreten und behoben:

- Der Lauf des Zeitplans meldete sein Ergebnis, und das **Neuzeichnen
  überschrieb die Meldung sofort wieder**. Ein Lauf ohne Modell sah damit aus,
  als sei nichts passiert — der Nutzer drückt dann noch dreimal.
- Der letzte Bericht im Zeitplanblock blieb nach einem Lauf von Hand **stehen**.
  „Zuletzt: 09:39“ neben einem Ergebnis von 09:44 liest sich wie ein Fehler.

## 5. Kann er es rückgängig machen?

Wo ja, braucht es keine Rückfrage. Deshalb hat der Rückblick einen Knopf
„Zurücknehmen“, der die Quellen wieder hervorholt
([`12`](12-zusammenfassung.md)) — und deshalb *muss* er ihn haben, denn ohne
wäre das Zusammenfassen eine Einbahnstraße.

Wo nein, wird vorher gefragt: Alles Außenwirksame geht über eine Freigabe mit
Trockenlauf ([`03`](03-delegation.md)).

## 6. Ist die Vorgabe die richtige?

Die meisten ändern nichts. Was voreingestellt ist, ist damit die Entscheidung
für fast alle.

| Vorgabe | Wert | Warum |
| --- | --- | --- |
| Mailports | 993 / 587 | Die verschlüsselten, nicht 143 / 25 |
| Zeitplan | aus | Kostet Geld und erzeugt Lärm ([`11`](11-zeitplan.md)) |
| Modell im Zeitplan | aus | Zweiter Schalter, weil er den Anbieter ruft |
| Sicherung im Lauf | an | Billigster Schritt, größter Nutzen |
| Ordnerzugriff | leer | Ein voreingestelltes Home wäre die Bequemlichkeit, die den Schutz aufhebt |

Die letzte Zeile ist die Ausnahme, die die Regel zeigt: Hier ist die bequemste
Vorgabe die falsche.

## Wo der Grundsatz endet

Er hebt die Sicherheitszusagen **nicht** auf. „Einfach“ heißt nie: ungefragt
etwas Außenwirksames tun, stillschweigend Fakten in den Bestand schreiben, oder
einen Ordner freigeben, den niemand freigegeben hat.

Wenn Einfachheit und Kontrolle sich widersprechen, gewinnt Kontrolle — aber dann
ist die Aufgabe, den kontrollierten Weg *einfacher zu machen*, nicht ihn
aufzugeben. Eine Rückfrage, die sein muss, soll in einem Satz verständlich sein
und mit einem Klick beantwortbar.

## Was offen ist

**Kein nativer Ordnerdialog.** Siehe Punkt 3. In der Tauri-App wäre er möglich
und wäre besser; er braucht ein Plugin und eine Berechtigung, und im Container
bliebe es beim Tippen. Die Auswahllisten nehmen den größten Teil des Schmerzes.

**Kein Erkennen über DNS.** `guess()` kennt ein Dutzend Anbieter über die
Adressendung. Wer eine eigene Domain bei einem der großen Anbieter hat, wird
nicht erkannt. Autodiscover über SRV-Einträge wäre der nächste Schritt.

## Verwandte Dokumente

- [`../CLAUDE.md`](../CLAUDE.md) — der Grundsatz im Wortlaut
- [`09-einrichtung.md`](09-einrichtung.md) — was eingerichtet wird und wo es liegt
- [`03-delegation.md`](03-delegation.md) — warum manche Rückfragen bleiben müssen
