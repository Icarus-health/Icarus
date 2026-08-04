# Qualitätsgates

> **Status:** verbindlicher Merge-Vertrag
> **Stand:** 2026-08-03
> **Betroffene Komponenten:** Entwicklung, CI und Release
> **Zuletzt gegen Code geprüft:** 2026-08-03

## Universelle Gates

Jeder PR muss:

- genau ein Aufgabenpaket erfüllen;
- innerhalb der erlaubten Pfade bleiben;
- ohne reale Schlüssel, Konten oder Nutzerdaten testbar sein;
- neue oder geänderte Zusicherungen mit einer Sabotageprobe schützen;
- `make verify` bestehen;
- den Nutzerweg und den Fehlerweg prüfen;
- Dokumentation aktualisieren, wenn sich ein Vertrag ändert;
- einen nachvollziehbaren Rückrollweg besitzen.

Warnungen, übersprungene Tests und tolerierte Fehler zählen als offene Punkte,
wenn das Aufgabenpaket sie nicht ausdrücklich und begründet erlaubt.

## Gate A – existenzielle Grenzen

Zusätzlich zu den universellen Gates:

- **Gedächtnis und Schema:** Migration vorwärts, Rückwärtslesbarkeit des letzten
  unterstützten Formats, deterministischer Neuaufbau und kein Wiederauferstehen
  gelöschter Daten.
- **Backup und Restore:** vollständiger Roundtrip, Prüfsummenfehler,
  beschädigtes Archiv, Abbruch in der Mitte und Erhalt des vorherigen Zustands.
- **Geheimnisse:** Tests erzwingen einen isolierten Test-Keychain; kein Zugriff
  auf den Schlüsselbund des Rechners und kein echter Provideraufruf.
- **Freigaben:** Vorschlag bis Ergebnis über den einzigen produktiven Nutzerweg;
  Ablehnung mutiert nichts, falsche Bestätigung führt nichts aus, eine Freigabe
  ist nur einmal einlösbar.
- **Workflows und Außenwirkung:** Neustart an jedem dauerhaften Zustand,
  höchstens einmalige Ausführung und sichtbare manuelle Klärung bei unklarem
  Ergebnis.
- **Browser, Dateien und Netzwerk:** Pfad- und SSRF-Sabotage, Weiterleitungen,
  Prompt Injection, Upload/Download-Grenzen und Geheimnisfelder.
- **Release:** echte Zielarchitektur, Paketinhalt, Start, Restore und – sobald
  öffentlich verteilt – Signatur, Notarisierung und Update-Rollback.

Klasse A wird nicht durch Zeitdruck oder ein grünes Modul-Testset herabgestuft.

## Gate B – Kernfunktion

- Unit-Test der reinen Logik;
- Integrationstest am echten Router, Store oder Adapter;
- echter Browserlauf bei UI-Änderungen;
- begrenzte Anfragen, Laufzeit und Ressourcen bei automatisch wiederholten
  Vorgängen;
- verständlicher leerer Zustand, Fehlerzustand und Wiederholungsweg;
- kein zweiter Speicher-, Policy- oder Identitätspfad.

## Gate C – mechanische Änderung

- Syntax, Format und Typprüfung der betroffenen Sprache;
- gezielter Regressionstest oder nachvollziehbarer Nachweis, warum vorhandene
  Tests die Änderung vollständig abdecken;
- keine unerwarteten Änderungen an generierten oder fremden Dateien.

## Gate für Ausführungsmodelle

Vor einer Rollenvergabe wird die eingefrorene Suite unter
`tasks/qualification/` gegen die Einreichung ausgeführt. Das zu prüfende Modell
erhält nicht die getrennten deterministischen Tests und bewertet sich nicht
selbst.

Der Bericht muss Suite-Version, Commit, UTC-Datum, Laufkennung, Rollenklasse,
Laufzeit, Kosten und Teilwerte enthalten. Die festen Gewichte sind:
Korrektheit 50 %, Testqualität 20 %, Scope-Treue 15 %, Sicherheit 10 % und
Dokumentation 5 %. Ein kritischer Scope- oder Sicherheitsverstoß sperrt die
Qualifikation unabhängig vom Gesamtwert.

`make qualify-execution-model` prüft Suite, Grader und Sabotageproben. Mit
`QUALIFICATION_SUBMISSIONS` bewertet derselbe Befehl einen vollständigen Lauf.
Alle Aufgaben verwenden synthetische Daten, feste Zeitlimits und keine realen
Schlüssel, Konten, Provider oder Netzwerkabhängigkeiten. Das Werkzeug weist nur
B, C oder `nicht_qualifiziert` aus; Klasse A kann dadurch nicht erworben werden.

## Lokale Standardprüfung

```bash
make sidecar-dev
make verify
```

`make verify` prüft Sidecar-Tests, Schema, Rust und die Syntax aller produktiven
JavaScript- und Browser-Testdateien. Container-, echte Browser-, Paket- und
Plattformläufe bleiben zusätzliche Gates der jeweiligen Aufgabe und der CI.

## Merge-Sperren

Nicht mergefähig ist ein PR mit:

- unklarer Risikoklasse oder fehlendem Aufgabenpaket;
- geänderten Pfaden außerhalb des Pakets ohne neue Freigabe;
- echtem Netzwerk- oder Keychain-Zugriff in Tests;
- einem nur behaupteten, nicht ausgeführten Prüfkommando;
- fehlendem Negativtest für eine neue Sicherheitszusage;
- manueller Nacharbeit, die ein normaler Nutzer bei jedem Update wiederholen
  müsste;
- einem offenen Konflikt zwischen Code, Test und Dokumentation.

## Reale Abnahme

CI beantwortet, ob sich das System deterministisch wie zugesagt verhält. Sie
beantwortet nicht, ob ein normaler Mensch Icarus versteht. Neue Kernabläufe
brauchen deshalb zusätzlich einen moderierten Nutzertest; Releases brauchen
einen Start-, Onboarding-, Backup- und Restore-Lauf aus dem tatsächlich
verteilten Artefakt.
