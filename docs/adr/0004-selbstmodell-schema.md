# ADR 0004: Eigenes Selbstmodell-Schema statt Fremdlösung

**Status:** akzeptiert · **Datum:** 2026-07-30

## Kontext

Säule 1 der Produktthese verlangt ein **überprüfbares** digitales Modell des Nutzers. Die Recherche kam zu dem Ergebnis, dass ein domänenübergreifendes Provenienzmodell in **keinem** der untersuchten Systeme vollständig umgesetzt ist.

Mem0 kommt am weitesten: Zeitstempel, Ablaufdaten, Graph Memory, Audit-Ansichten im Self-Hosted-Server. Aber es bleibt ein Faktenspeicher. Es beantwortet „Was weiß ich über diese Person?" gut und „Woher weiß ich das, gilt es noch, und was passiert, wenn die Quelle wegfällt?" nicht.

Genau an diesen Fragen scheitern langfristige Assistenten in der Praxis — die Fähigkeiten, die LongMemEval als Wissensupdates, temporales Reasoning und Abstention misst.

## Entscheidung

Ein **eigenes, speicherunabhängiges Schema**: [`schema/self-model.schema.json`](../../schema/self-model.schema.json), JSON Schema Draft 2020-12.

Kernpunkte:

- Jede Aussage trägt **Pflicht-Provenienz** (`source_type`, optional Quellverweis, Erfassungszeit, extrahierendes Modell, wörtlicher Beleg). Ohne Herkunft kommt nichts ins Modell.
- **Ersetzung statt Überschreibung** über `supersedes` / `superseded_by`. Die Kette bleibt lesbar.
- **Zeitliche Gültigkeit** über `valid_from`, `expires_at`, `last_confirmed_at`.
- **Ableitungen sind markiert** über `source_type: inference` plus `derived_from`.
- **Widerruf kaskadiert** über `redaction.cascade`; ein Grabstein bleibt stehen.
- **Aussagetypen** (`kind`) trennen stabile Identität von veränderlichem Zustand.
- **Schutzbedarf** über `sensitivity` steuert, was an externe Modelle gehen darf.

Das Schema entsteht bewusst **vor** dem Code, der es füllt. Ein Provenienzmodell nachzurüsten bedeutet, jede bereits gespeicherte Aussage ohne Herkunft dastehen zu lassen — der Schaden ist dann nicht mehr reparabel, weil die Herkunft nirgends mehr existiert.

## Warum nicht die Formate der anderen

Lettas `.af` beschreibt einen **Agentenzustand**, nicht ein überprüfbares Personenmodell — und Letta entfällt ohnehin ([ADR 0003](0003-kein-letta.md)). Mem0s internes Format ist an Mem0 gebunden; wird die Ablage getauscht, wandert das Modell nicht mit. Genau diese Bindung soll Säule 2 vermeiden.

## Konsequenzen

**Eigener Pflegeaufwand.** Das Schema muss versioniert und migriert werden — `schema_version` ist dafür vorgesehen. Über 20 Jahre ist das ein reales, dauerhaftes Kostenelement.

**Abbildung auf Mem0 ist ungeklärt.** Ob sich Ersetzungsketten und kaskadierende Löschung sauber auf Mem0 abbilden lassen, ist der nächste zu prüfende Punkt. Fällt die Antwort negativ aus, braucht Säule 1 einen eigenen Speicher neben Mem0.

**Das Schema erzwingt nichts von allein.** Es kann Widersprüche darstellen, aber nicht auflösen. `sensitivity` ist im Schema eine Markierung; seit 2026-07-30 wird sie in der Agentenschicht durchgesetzt — siehe [06-gedaechtnis-kontrakt.md](../06-gedaechtnis-kontrakt.md). Die übrige Durchsetzung gehört in die Policy-Schicht ([03-delegation.md](../03-delegation.md)).

**Geprüft wird automatisiert.** `make validate-schema` validiert das Beispielprofil gegen das Schema. Das Beispiel deckt absichtlich die schwierigen Fälle ab: Ersetzungskette, abgelaufene Gesundheitsangabe, markierte Ableitung, kaskadierender Widerruf.
