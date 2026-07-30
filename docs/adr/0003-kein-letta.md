# ADR 0003: Letta wird nicht verwendet

**Status:** akzeptiert · **Datum:** 2026-07-30

## Kontext

Die Ausgangsrecherche ([00-recherche.md](../00-recherche.md)) empfahl **Letta** als „beste Basis für stateful agent core" — begründet mit rund 24.000 Sternen, 176 Releases und einer Memory-Hierarchie aus In-Context-Blöcken, Archival Memory, Compaction und dem Exportformat `.af`. Konzeptionell passt das ausgezeichnet zur Vision eines langlebigen persönlichen Gesprächspartners.

Vor der Aufnahme als Kernabhängigkeit wurde die Empfehlung gegen die Primärquellen geprüft. Das Ergebnis widerspricht ihr.

## Befunde

**Das Repo ist deprecated.** `AGENTS.md` in `letta-ai/letta` beginnt mit der Überschrift „This repository is deprecated" und führt aus:

> This repository contains the **legacy Letta server**: the self-hosted API server (`letta/letta` image) that powers the Letta V1 API and V1 SDKs. It is in maintenance mode and is no longer where active development happens.

Das `README.md` bestätigt das und verweist für Self-Hosting auf den App Server.

**Die Aktivitätssignale gehören zu einem anderen Projekt.** Aktive Entwicklung findet in `letta-ai/letta-code` statt — einer TypeScript-CLI, die Agenten lokal im Terminal betreibt. Das ist kein selbst hostbares Memory-Backend und nicht das, was die Recherche als Agent-Core beschrieben hat. Die im Report zitierte „Aktivität vor wenigen Stunden" bezieht sich mit hoher Wahrscheinlichkeit hierauf.

**Der Nachfolger ist cloud-gekoppelt.** Der App Server, der den Legacy-Server ersetzen soll, baut laut Dokumentation eine ausgehende WebSocket-Verbindung zu **Constellation** (Lettas Agent-Cloud) auf. Die Authentifizierung läuft über OAuth auf den Pro-, Max-lite- und Max-Tarifen; nur auf Developer-Tarifen lässt sich ersatzweise `LETTA_API_KEY` setzen.

**Das Legacy-Image altert.** `letta/letta` war zum Prüfzeitpunkt seit rund drei Monaten nicht aktualisiert.

## Entscheidung

**Letta wird nicht Teil der Architektur** — weder als Kern noch als optionales Compose-Profil.

Der Ausschlag gibt nicht die Deprecation allein, sondern die Kombination: Der einzige weiterhin selbst hostbare Pfad ist ein Legacy-Artefakt in Wartungsmodus, und der unterstützte Pfad setzt eine Cloud-Anbindung samt Konto und Tarif voraus. Beides ist mit der Produktthese — ein Gedächtnis, das **unabhängig von einzelnen KI-Anbietern** verwaltet wird — nicht vereinbar. Eine Komponente, die das Gedächtnis über Jahre halten soll, ausgerechnet auf einer abgekündigten Grundlage aufzubauen, kehrt den Zweck des Projekts um.

## Konsequenzen

**Der Agent-Core bleibt vorerst leer.** Die Gesprächsführung übernimmt Open WebUI, die Gedächtnisverwaltung Mem0. Ob Icarus dauerhaft eine eigene Orchestrierungsschicht bekommt, ist die größte offene Architekturfrage ([04-roadmap.md](../04-roadmap.md)).

**Konzeptionell bleibt Letta wertvoll.** Die Ideen — getrennte Memory-Tiers, aktive Speicherverwaltung, ein exportierbares Agentenformat — fließen in das [Selbstmodell](../02-selbstmodell.md) ein. Übernommen wird die Architekturidee, nicht die Abhängigkeit.

**Neu zu bewerten, wenn** der App Server einen vollständig lokalen Betrieb ohne Konto und ohne Cloud-Verbindung unterstützt.
