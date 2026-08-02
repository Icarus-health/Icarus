# Dokumentationslandkarte

> **Status:** verbindlicher Einstieg für Mitwirkende  
> **Stand:** 2026-08-02

Icarus unterscheidet bewusst zwischen Zielbild, aktuellem Systemvertrag und
historischen Architekturentscheidungen. Neue Mitwirkende lesen Dokumente in
dieser Reihenfolge:

1. [`00-produktvision.md`](00-produktvision.md) – langfristiger Zielzustand.
2. [`00a-erste-produktstufe.md`](00a-erste-produktstufe.md) – erster
   marktfähiger Nutzen.
3. [`01-architektur.md`](01-architektur.md) – heutige Referenzarchitektur.
4. den Vertrag des betroffenen Subsystems.
5. relevante ADRs für den historischen Entscheidungsweg.

## Verbindliche aktuelle Dokumente

| Dokument | Rolle |
|---|---|
| `00-produktvision.md` | Vollvision und unverhandelbare Produktprinzipien |
| `00a-erste-produktstufe.md` | Zielgruppe, Kernabläufe und Erfolgskriterien |
| `01-architektur.md` | heutige technische Referenzarchitektur |
| `02-selbstmodell.md` | Vertrag des überprüfbaren Selbstmodells |
| `03-delegation.md` | Policy, Freigaben und Audit |
| `04-roadmap.md` | aktuelle Entwicklungsreihenfolge |
| `05-sicherheit.md` | Bedrohungsmodell und Schutzmaßnahmen |
| `06-gedaechtnis-kontrakt.md` | verbindliche Regeln für Wissen |
| `08-gedaechtnisschichten.md` | Kurzzeit, Episoden und Bestand |
| `09-einrichtung.md` | aktueller Einrichtungsfluss |
| `10-verdichtung.md` | Vorschläge zwischen Episode und Aussage |
| `11-zeitplan.md` | mitlaufender Prozess |
| `12-zusammenfassung.md` | reversible Langzeitverdichtung |
| `13-nutzerfreundlichkeit.md` | konkrete UX-Regeln |
| `14-posteingang.md` | Mail im Gesprächsfenster |
| `15-loslegen.md` | aktueller Container-Einstieg |

## ADRs

Dokumente unter `adr/` erklären, warum eine Entscheidung zu ihrem Zeitpunkt
getroffen wurde. Sie sind historische Belege, keine automatisch aktuelle
Systembeschreibung. Bei einem Konflikt gilt:

1. Code und Tests,
2. aktueller Systemvertrag,
3. Produktvision,
4. ADR als Entscheidungshistorie.

## Pflegepflicht

Jedes verbindliche Dokument nennt künftig:

- Status,
- Gültigkeitsdatum,
- betroffene Komponenten,
- Datum der letzten Prüfung gegen den Code.

Eine Änderung ist nicht fertig, wenn Code und Dokumentation unterschiedliche
Entwicklungsstände beschreiben.
