# Gedächtnis-Kontrakt

Stand 2026-07-30. Verbindlich für `sidecar/icarus_memory`.

Dieses Dokument hält die Regeln fest, unter denen Icarus etwas als gewusst
behandeln darf. Es entstand aus einem Abgleich zwischen dieser Umsetzung und
einer unabhängig entwickelten Spezifikation für dieselbe Aufgabe. Drei Lücken
sind geschlossen, fünf sind benannt und offen.

## Der Maßstab

> Icarus sagt lieber „das weiß ich nicht" als einen selbstbewusst falsch
> einsortierten Fakt.

Vergleichbare Produkte halluzinieren nicht in erster Linie wegen schwacher
Modelle. Sie scheitern, weil sie Text ohne Identität, Zeit und Herkunft
speichern. Ein im März gelernter Fakt wird später als Gegenwart behauptet. Eine
Korrektur überschreibt stillschweigend ihren Vorgänger, und der Widerspruch wird
unsichtbar.

Deshalb gilt hier: jede Regel unten ist durch einen Test gedeckt, und jeder Test
wurde durch absichtliche Sabotage geprüft — die Umsetzung kaputtmachen, den
erwarteten Fehlschlag sehen, zurückbauen. Ein Test, der noch nie
fehlgeschlagen ist, beweist nichts.

## Was durchgesetzt ist

### 1. Die Aussagenschicht ist append-only

`SqliteBackend.put()` schrieb bisher `ON CONFLICT(id) DO UPDATE SET …
statement=excluded.statement`. Der Rohinhalt einer Aussage war damit
überschreibbar, und die Ersetzungskette war nur eine Konvention.

Jetzt gilt: Statuswechsel bleiben erlaubt — davon leben Ersetzung, Ablauf und
Bestätigung. Unveränderlich sind `id`, `recorded_at`, `kind`, `statement` und die
Herkunft. Die einzige Ausnahme ist der Widerruf: Löschen auf Wunsch der Person
muss möglich bleiben, ist an `status = redacted` erkennbar und hinterlässt einen
Grabstein.

Zwei Ebenen, absichtlich unabhängig:

- `backends.ensure_content_unchanged()` prüft vor jedem Schreibvorgang, in
  beiden Backends.
- Zwei SQLite-Trigger (`assertions_inhalt_unveraenderlich`,
  `assertions_kein_loeschen`) halten die Regel auch gegen jeden, der die
  Bibliothek umgeht — `sqlite3` auf der Kommandozeile, ein anderes Programm, ein
  späterer Codeweg. Bestehende Dateien werden beim Öffnen nachgezogen.

`MemoryBackend` legt seit dieser Änderung Kopien statt Referenzen ab. Sonst
hätte eine Änderung am übergebenen Objekt den Bestand still mitverändert und die
Regel wäre dort nicht prüfbar gewesen.

Tests: `tests/test_append_only.py` (11).

### 2. Sensible Fakten gehen nicht automatisch nach draußen

`Agent` hob die Obergrenze auf `sensitive`, `shareable()` filterte entsprechend,
und in `providers.py` gab es keine Prüfung. Als `sensitive` markierte Aussagen
gingen damit an OpenAI oder Anthropic. Nur `special_category` blieb zurück.

Jetzt folgt die Grenze dem Anbieter, nicht der Bequemlichkeit:

| Anbieter | Obergrenze |
| --- | --- |
| Loopback (`is_local_endpoint`) | `sensitive` |
| alles andere | `normal` |
| kein Anbieter gesetzt | `normal` |

`special_category` bleibt in **beiden** Fällen zurück. Der Aufrufer darf die
Grenze über `max_sensitivity` senken, aber nie über das heben, was der Anbieter
verdient — `Agent.effective_sensitivity_ceiling()` bildet das Minimum.

Nur Loopback zählt als lokal. Ein Ollama im Heimnetz ist bereits ein Netzwerk,
und ein Hostname, der heute auf `127.0.0.1` zeigt, kann morgen umziehen; die
Adresse wird deshalb literal geprüft und nicht aufgelöst.

Dazu eine zweite, unabhängige Prüfung: `Agent.assert_egress_allowed()` sieht sich
unmittelbar vor dem Versand die fertige Nutzlast an und vergleicht sie gegen die
Aussagen über der Grenze. Damit kann kein anderer Codeweg — ein
Werkzeugergebnis, ein Verlauf aus einer früheren Runde, eine künftige
Erweiterung — etwas hinaustragen, das hier nie erlaubt war. Verweigerungen
landen als `refused` im Audit-Log und lösen `EgressBlocked` aus. Fail-closed.

Tests: `tests/test_egress.py` (17).

### 3. Ein Fakt aus dem März wird nicht als Gegenwart behauptet

`Agent.context()` schrieb `- [kind] statement`. Kein Alter, keine Quelle.
`last_confirmed_at` wurde von `confirm()` gesetzt und auf keinem Ausgabepfad
gelesen.

Jetzt trägt jeder Fakt seine Quellenkennung und ein Aktualitätsurteil
(`currency.py`). Der Horizont hängt an der **Art** der Aussage, nicht an einer
globalen Zahl — eine globale Schwelle wäre in beide Richtungen falsch:

| Art | angealtert nach | veraltet nach |
| --- | --- | --- |
| `state` | 7 Tage | 365 Tage |
| `goal` | 90 Tage | 730 Tage |
| `episode` | 90 Tage | nie (Vergangenes bleibt wahr) |
| `identity`, `relationship`, `skill` | 365 Tage | 1825 Tage |
| `preference`, `constraint` | 365 Tage | 1095 Tage |
| unbekannte Art | 30 Tage | 365 Tage |

Als Bezugszeitpunkt gilt der jüngste Beleg: `last_confirmed_at`, sonst
`valid_from`, sonst `provenance.captured_at`, sonst `recorded_at`. Eine
Bestätigung wiegt am schwersten — sie ist die ausdrückliche Aussage der Person,
dass es weiterhin gilt.

`expires_at` bleibt davon unberührt. Es ist die *ausdrückliche* Frist des
Aufrufers und wird von `usable()` durchgesetzt. Die Horizonte sind die
*abgeleitete* Alterung für alles, wo niemand eine Frist gesetzt hat — und das
ist der Normalfall.

In der Ausgabe:

```
Was du über den Nutzer weißt:
- [preference] Mag ruhige, klare Oberflächen. (chat:12)
- [state] Icarus hängt an der Notarisierung. (chat:31, Stand 2026-07-13, womöglich veraltet)
- [goal] Will Icarus im Herbst ausliefern. (chat:40, bestätigt 2026-07-31)

Alte Angaben — nicht als aktuell behaupten, im Zweifel nachfragen:
- [state] Wohnt gerade in einer Ferienwohnung. (chat:3, Stand 2025-06-06)

(Eine weitere Aussage ist als besonders geschützt markiert und wird dir nicht übermittelt.)
```

Veraltetes wird getrennt gezeigt, nicht weggelassen: der Nutzer darf nach seiner
eigenen Vergangenheit fragen. Aber diese Zeilen dürfen nie als Gegenwart
auftreten, und dafür braucht es die eigene Überschrift. Der `SYSTEM_PROMPT`
erklärt beides — eine Kennzeichnung nützt nichts, wenn dem Modell die Regel
fehlt.

Tests: `tests/test_aktualitaet.py` (16).

## Was offen ist

Ehrlich benannt, nach Wirkung geordnet. Keiner dieser Punkte ist heute durch
einen Test gedeckt.

**Kein Digest an der Quelle — teilweise geschlossen.** `Provenance` hat weiterhin
keinen Hash. Die neue Episodenschicht trägt ihn jedoch: `Episode.digest` ist ein
SHA-256 des Rohinhalts, und die Entdopplung hängt an einem UNIQUE-Index darauf,
nicht an einer Prüfung im Code (`episodes.py`, `tests/test_episoden.py`). Damit
ist der Digest für alles vorhanden, was über die Aufnahme kommt. Offen bleibt er
für Aussagen, die auf anderem Weg entstehen, und die Neuprüfung darauf ist noch
nicht gebaut. Siehe [`08-gedaechtnisschichten.md`](08-gedaechtnisschichten.md).

**Keine Neuprüfung vor der folgenreichen Aktion.** `Policy.grant()` vergleicht
die getippte Bestätigungsphrase. Die Quelle wird nicht erneut geöffnet. Ein
Vorschlag, der auf einer inzwischen geänderten Datei oder Mail beruht, wird
ausgeführt, als wäre nichts passiert. Der Digest ist die Voraussetzung dafür.

**Kein `disputed`-Status — geschlossen.** `Status` kennt ihn jetzt.
`store.dispute()` markiert zwei oder mehr Aussagen als einander widersprechend,
und zwar **gegenseitig**: Ein einseitiger Verweis hieße, dass eine Seite des
Widerspruchs unbemerkt als Gegenwart auftritt. Strittiges ist nicht `usable()`
und erscheint im Kontext unter einer eigenen Überschrift („Ungeklärt"), damit
das Modell nachfragt statt zu wählen.

Aufgelöst wird über die bestehenden Wege — `record(supersedes=…)` oder
`retract()`. Ein eigener „Streit beilegen"-Pfad wäre ein dritter Weg, Bestand zu
ändern, und davon gibt es genug. Was **offen bleibt**, ist das automatische
*Finden* von Widersprüchen; heute muss sie jemand benennen.

Tests: `tests/test_disputed.py` (10).

**Der abgeleitete Zustand wird nicht neu berechnet.** `Assertion.is_usable()`
vertraut dem gespeicherten `status`; `usable()` schreibt ihn beim Lesen sogar
zurück. Ein Bestand, dessen Status durch einen Fehler falsch gesetzt wurde,
bleibt falsch. Eine Projektion, die aus der Rohschicht neu berechnet, würde das
korrigieren und wäre prüfbar durch doppeltes Abspielen.

**Keine Zitatpflicht im Werkzeugpfad.** `context()` trägt jetzt Quellen. Das
Werkzeug `gedaechtnis_suchen` → `recall()` gibt weiterhin nur die Quellen*art*
aus („chat"), nicht die Kennung. Es gibt keinen Mechanismus, der eine Ausgabe
ohne Beleg verhindert.

## Vorgehen

Die Reihenfolge ergibt sich aus den Abhängigkeiten: der Digest zuerst, weil die
Neuprüfung darauf aufsetzt; dann `disputed`; dann die Projektion.

Für jeden Punkt gilt dasselbe Verfahren wie bei den drei geschlossenen:

1. Regel in dieses Dokument schreiben.
2. Test schreiben, der den heutigen Zustand belegt und fehlschlägt.
3. Umsetzen, bis er grün ist.
4. Umsetzung absichtlich kaputtmachen und prüfen, dass der Test mit der
   erwarteten Meldung fehlschlägt. Zurückbauen.
5. Volle Suite fahren.

## Verwandte Dokumente

- [`01-architektur.md`](01-architektur.md) — die zwei Speicher und ihr Verhältnis
- [`02-selbstmodell.md`](02-selbstmodell.md) — Datenmodell und Korrekturoperationen
- [`03-delegation.md`](03-delegation.md) — Aktionsklassen und Freigabestufen
- [`05-sicherheit.md`](05-sicherheit.md) — fremde Inhalte und Prompt Injection
- [`08-gedaechtnisschichten.md`](08-gedaechtnisschichten.md) — Kurzzeit, Mittelfrist, Bestand
