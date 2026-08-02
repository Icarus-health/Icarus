# Roadmap

> **Status:** aktuelle Entwicklungsreihenfolge  
> **Stand:** 2026-08-02  
> **Zuletzt gegen Code und Produktvision geprüft:** 2026-08-02  
> **Ersetzt:** den datumsgebundenen MVP-Plan der frühen Alpha

## Ausgangslage

Der Kern ist end-to-end vorhanden:

- überprüfbares Selbstmodell,
- Episoden und menschlich bestätigte Verdichtung,
- Projekte, Aufgaben und Notizen,
- Mail, Kalender, Dateien und einfacher Webabruf,
- Policy, Freigaben, Audit und Prompt-Injection-Begrenzung,
- Tauri-App, Containerweg und MCP,
- vollständiges lokales Installations-Backup.

Icarus ist trotzdem keine Consumer-Beta. Die größten Risiken sind heute
Nutzererlebnis, reale Integrationsstabilität, Dokumentationsdrift und der Sprung
vom reaktiven Werkzeug zum täglich nützlichen Chief of Staff.

## Release 0.2 – Konsolidierter Kern

**Ziel:** Eine Wahrheit, eine Sicherung, ein verständlicher Entwicklungsstand.

- [x] Produktvision und erste Produktstufe dokumentieren
- [x] Schema und Laufzeit für `disputed` synchronisieren
- [x] Schema-Parität automatisiert testen
- [x] alle lokalen Datenbanken und Einstellungen sichern
- [x] Manifest, Prüfsummen und vollständigen Restore testen
- [x] Referenzdokumente auf den tatsächlichen Stand bringen
- [ ] CI-Lauf auf allen unterstützten Python-Versionen grün
- [ ] Wiederherstellung über die echte Oberfläche im Browser prüfen
- [ ] Sicherung auf macOS und im Container manuell testen

## Release 0.3 – Täglicher Chief of Staff

**Ziel:** Drei reale Kernabläufe tragen für erste Nutzer.

1. Tagesprioritäten verstehen.
2. Termine mit vollständigem Kontext vorbereiten.
3. Offene Schleifen erkennen und nächste Schritte vorbereiten.

Arbeitspakete:

- „Heute“ von einer Kartenübersicht zu einer priorisierten Handlungsliste
  entwickeln;
- Entscheidungen, Freigaben und Gedächtnisvorschläge in einer verständlichen
  Inbox zusammenführen;
- Projektabhängigkeiten, Wartestatus, Risiken und nächste Handlung ergänzen;
- Terminbriefing aus Projekt, Personen, Mail, Aufgaben und Entscheidungen;
- Nachbereitung mit vorgeschlagenen Aufgaben und Notizen;
- Onboarding nach gewünschtem Nutzen statt nach technischer Infrastruktur;
- fünf bis zehn reale Nutzer über mehrere Wochen instrumentiert testen.

## Release 0.4 – Belastbarer digitaler Zwilling

**Ziel:** Das Selbstmodell wird relational, zeitlich und für Menschen
verständlich.

- verbindliches Entitätenmodell für Personen, Organisationen, Rollen, Projekte,
  Ziele, Entscheidungen und Ereignisse;
- Graph als ableitbare Projektion, nicht als zweite Wahrheit;
- Identitätsauflösung und Dublettenbehandlung;
- Zeitachse und Entwicklung von Aussagen;
- Ziele, Werte und bestätigte Gewohnheiten;
- persönliche Relevanzlogik und Aufmerksamkeitssteuerung;
- verständliche Oberfläche für Quelle, Alter, Unsicherheit und Konflikt.

## Release 0.5 – Modell-Harness und Außenwelt

**Ziel:** Modelle sind tatsächlich austauschbare, bewertete Antriebe.

- Fähigkeitsprofile und Modellregistry;
- Routing nach Qualität, Datenschutz, Kosten, Latenz und Werkzeugfähigkeit;
- Fallback und Circuit Breaker;
- Evaluationsdatensatz aus realen Icarus-Aufgaben;
- Suche, Quellenvergleich und Datumslogik für aktuelle Informationen;
- beobachtbare Nachrichten- und Forschungsaufträge mit persönlicher Relevanz.

## Release 0.6 – Universelle Handlungsschicht

**Ziel:** Mehr digitale Arbeit erledigen, ohne die Kontrolle zu verlieren.

- Connector-SDK mit Wirkungsmanifest;
- langlebige Workflows mit Warten, Wiederholung und Fehlerbehandlung;
- Browserautomation;
- kontrolliertes Computer-Use;
- differenzierte Policy für Veröffentlichung, Geld, Recht, neue Empfänger und
  irreversible Schritte;
- benannte, zeitlich begrenzte Dauerberechtigungen.

## Release 1.0 – Persönliches KI-Betriebssystem

- signierte und zuverlässig aktualisierbare Desktop-App,
- Windows neben macOS,
- sichere Geräteidentität und verschlüsselte Synchronisation,
- mobile und sprachbasierte Zugänge,
- Erweiterungsökosystem,
- dokumentierte Migration und Wiederherstellung über mehrere Versionen,
- nachgewiesene Consumer-Nutzbarkeit.

## Release-Gates

Eine Stufe beginnt erst, wenn die vorherige im realen Ablauf trägt:

- Tests und Sabotageproben sind grün.
- Dokumentation beschreibt denselben Stand wie der Code.
- Sicherung und Wiederherstellung funktionieren.
- Ein normaler Nutzer versteht Erfolg und Fehler.
- Der Kernablauf wurde außerhalb der Entwicklungsumgebung geprüft.
- Neue Funktionen erzeugen keine zweite Speicher-, Policy- oder
  Identitätsschicht.

## Nicht priorisiert

Bis Release 0.3 sind neue Modellanbieter, dekorative Graphansichten,
allgemeines Computer-Use und weitere unverbundene Konnektoren nachrangig. Der
größte Wert entsteht zunächst nicht durch Reichweite, sondern durch einen
Chief-of-Staff-Kreislauf, den Menschen täglich freiwillig nutzen.
