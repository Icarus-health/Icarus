# Vom Gedächtnis zum Stabschef

Icarus ist als **Gedächtnis** gebaut: es merkt sich, woher etwas kommt, und
schreibt nichts in den Bestand, was ein Mensch nicht angenommen hat. Das ist
die Grundlage und bleibt sie.

Ein Stabschef ist mehr. Er weiß nicht nur, was war — er sagt, was heute zählt,
er behält im Blick, worauf du wartest, er kennt die Menschen um dich herum, und
er merkt, wenn eine Entscheidung auf einer Annahme stand, die nicht mehr gilt.

Dieses Papier beschreibt den Weg dorthin: fünf Etappen, jede für sich nützlich,
jede ohne die nächste sinnvoll. Was hier steht, ist der Plan — der Zielzustand
steht in [`00-produktvision.md`](00-produktvision.md), die Gestaltung in
[`16-gestaltung.md`](16-gestaltung.md).

## Der Maßstab

> Ein Stabschef, der jeden Morgen drei Punkte hat, weil das Format drei Punkte
> vorsieht, ist wertlos.

Jede Etappe wird daran gemessen, ob sie **eine Entscheidung abnimmt**, nicht
daran, ob sie eine Ansicht hinzufügt. Eine neue Liste ist keine Entlastung. Ein
Satz, der sagt, was zuerst dran ist, ist eine.

Und keine Etappe hebt die Zusage auf: **Verdichtung schlägt vor, sie schreibt
nicht.** Was hier an Urteil hinzukommt, urteilt über das, was schon dasteht —
es legt nichts an.

## Die fünf Etappen

### 1. Das Briefing — aus einer Übersicht wird ein Urteil ✓

Eine Übersicht listet auf, was es gibt, und überlässt die Gewichtung dem Leser.
Ein Briefing sagt, was zuerst dran ist, und begründet es.

Bewusst **ohne Modell**: ein Briefing, das nur mit eingerichtetem Anbieter
funktioniert, wäre am ersten Morgen leer. Die Rangfolge ist schlichter als ein
Modell, dafür läuft sie immer, offline, in Millisekunden — und sie ist
nachprüfbar. Ein Modell darf später die Formulierung übernehmen; die Rangfolge
sollte es nicht.

Siehe `sidecar/icarus_memory/briefing.py`.

### 2. Delegation — was bei jemandem liegt, ist keine Schuld ✓

Eine flache Aufgabenliste wirft zwei Dinge in einen Topf: was **du** noch tun
musst, und worauf du **wartest**. Beides ist offen, aber nur das erste ist
Arbeit für dich.

Eine Aufgabe trägt jetzt, bei wem sie liegt und seit wann. Solange dort ein
Name steht, ist sie nicht überfällig — sonst wächst eine rote Liste aus Dingen,
an denen man nichts ändern kann, und die Liste, die handlungsfähig machen soll,
wird zur Anklage.

Das Briefing fasst nach, aber erst nach vierzehn Tagen. Wer gestern etwas
abgegeben hat, will heute nicht daran erinnert werden — das wäre kein Stabschef,
sondern ein Wecker.

Derselbe Name setzt die Frist nicht zurück. Sonst ließe sich die eigene
Wartezeit wegdrücken, indem man das System noch einmal daran erinnert, worauf
man wartet.

Siehe `sidecar/icarus_memory/tasks.py`.

### 3. Menschen — abgeleitet, nicht angelegt

Ein Stabschef kennt die Menschen um den Chef herum. Icarus weiß aus den
Episoden längst, mit wem etwas zu tun hatte — es macht bisher nichts daraus.

Die Personenebene wird deshalb **abgeleitet**: kein neues Datenmodell, keine
neue Tabelle, kein Ort, an dem der Nutzer Menschen anlegt. Wer in Episoden
auftaucht, ist eine Person; was über ihn im Bestand steht, steht bei ihm.

Namen werden zusammengeführt, ohne zu raten. Gleicher Name ist dieselbe Person,
mehr nicht — falsch zusammengelegte Menschen sind schlimmer als zwei Einträge.

### 4. Entscheidungen und ihre Annahmen

Der eigentliche Wert eines Gedächtnisses zeigt sich, wenn eine Annahme kippt.

Heute kann Icarus sagen, dass ein Satz überholt ist. Es kann noch nicht sagen:
*„Du hast dich damals für X entschieden, weil Y galt. Y gilt seit gestern nicht
mehr."* Dafür muss eine Entscheidung ihre Grundlage kennen — nicht als Notiz,
sondern als Verweis auf die Aussagen, auf denen sie stand.

Das ist die Etappe, die aus einem Archiv einen Ratgeber macht. Und die einzige,
die eine echte Erweiterung des Datenmodells braucht.

### 5. Das Urteil — dringend gegen wichtig

Bis hierher ordnet Icarus nach Regeln: Fristen, Alter, Anzahl. Das reicht für
*dringend*. Es reicht nicht für *wichtig*.

Was wichtig ist, hängt an Zielen, und Ziele stehen im Bestand — als Aussagen
vom Typ `goal`. Diese Etappe braucht ein Modell, und sie ist deshalb die
letzte: alles davor muss ohne Modell nützlich sein.

Auch hier gilt die Grenze. Das Modell darf gewichten und formulieren. Es darf
nicht entscheiden, und es darf nichts anlegen.

## Wie angedockt wird

Die naheliegende Antwort auf „wir brauchen noch Dienst X“ ist ein weiterer
Konnektor. Nach dem fünften ist das ein Zweitberuf.

Der Weg ist stattdessen die **MCP-Tür in beide Richtungen**: Icarus ist heute
schon ein MCP-Server. Wird es zusätzlich MCP-*Client*, dockt jeder Dienst an,
für den irgendwer einen Server geschrieben hat — ohne dass hier eine Zeile
dafür entsteht.

Was dabei nicht verhandelbar ist: fremde Werkzeuge liefern **fremden Inhalt**.
Ihre Ausgabe ist `returns_untrusted`, sie hebt die Freigabestufe des Zuges, und
keine Dauerregel senkt sie wieder. Ein angedockter Dienst erweitert, was Icarus
kann — nicht, wem es glaubt.

## Was bewusst nicht kommt

**Kein Autopilot.** Nichts Außenwirksames ohne Rückfrage, auch nicht nach der
zwanzigsten gleichen Bestätigung. Dauerregeln senken die Stufe auf `notify` —
nie auf „ungefragt“.

**Keine Personenakte.** Die Personenebene zeigt, was ohnehin im Bestand steht,
gebündelt nach Namen. Sie legt nichts über Menschen an, was nicht jemand gesagt
oder angenommen hat.

**Kein Zwang zum Modell.** Jede Etappe außer der fünften läuft ohne Anbieter.
Wer keinen anschließt, verliert Formulierung — nicht Funktion.
