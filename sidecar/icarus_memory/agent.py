"""Der Assistent.

Verbindet die vier Säulen zu einem Ablauf:

1. Kontext aus dem **Selbstmodell** — nur gültige Aussagen, gefiltert nach
   Schutzbedarf. Besonders Geschütztes verlässt das Haus nicht.
2. Ein **austauschbares Modell** beantwortet die Anfrage.
3. Will es ein **Werkzeug** benutzen, geht der Wunsch durch die **Policy**.
4. Alles landet im **Audit-Log**.

Der Agent führt selbst nichts aus. Er stellt Anträge; ausgeführt wird erst,
wenn die Policy es erlaubt oder der Nutzer freigibt.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog
from .currency import Currency, describe, judge
from .model import Sensitivity, now
from .policy import (
    ActionClass,
    ApprovalLevel,
    PendingApproval,
    Policy,
    PolicyError,
    constraints_from_store,
)
from .providers import Provider, ProviderError, ToolCall
from .store import SelfModelStore
from .tools import Tool


class EgressBlocked(Exception):
    """Etwas über der Schutzbedarfsgrenze stand in einer ausgehenden Nutzlast.

    Kein Fehler im üblichen Sinn, sondern die Sperre, die greift. Sie beendet
    den Zug absichtlich statt zu filtern und weiterzumachen: wer sie sieht, soll
    wissen, dass ein Codeweg etwas hinaustragen wollte, das er nicht durfte.
    """


_RANK: dict[Sensitivity, int] = {
    Sensitivity.NORMAL: 0,
    Sensitivity.SENSITIVE: 1,
    Sensitivity.SPECIAL_CATEGORY: 2,
}


def _lower_of(left: Sensitivity, right: Sensitivity) -> Sensitivity:
    return left if _RANK[left] <= _RANK[right] else right


SYSTEM_PROMPT = """Du bist Icarus, ein persönlicher Assistent mit langfristigem Gedächtnis.

Über den Nutzer weißt du nur, was unten steht. Rate nichts dazu.

Regeln:
- Nutze `merken` nur, wenn der Nutzer etwas über sich mitteilt, das über das
  aktuelle Gespräch hinaus gilt. Beiläufiges gehört nicht ins Gedächtnis.
- Für Fakten, die sich geändert haben können, und für alles Aktuelle nutze
  Werkzeuge statt zu raten. Das gilt besonders für Datum und Uhrzeit.
- Grenzen des Nutzers sind bindend. Erkläre, wenn etwas daran scheitert.
- Jeder Fakt unten trägt seine Quelle in Klammern. Steht dort „Stand <Datum>",
  ist die Angabe womöglich veraltet: behaupte sie nicht als Gegenwart, sondern
  nenne das Datum oder frage nach. Was unter „Alte Angaben" steht, gilt nur für
  die Vergangenheit — nutze es nie für eine Aussage über den heutigen Zustand.
- Sage „das weiß ich nicht" statt zu raten. Ein falsch einsortierter Fakt ist
  schlimmer als eine offene Frage.
- Antworte knapp und auf Deutsch."""


@dataclass
class Turn:
    """Das Ergebnis einer Runde."""

    reply: str = ""
    approvals: list[PendingApproval] = field(default_factory=list)
    """Anträge, die auf den Nutzer warten. Solange sie offen sind, ist die
    Runde nicht abgeschlossen."""

    notices: list[str] = field(default_factory=list)
    """Was ohne Rückfrage getan wurde — der Nutzer erfährt es hinterher."""

    used_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "approvals": [a.to_dict() for a in self.approvals],
            "notices": self.notices,
            "used_tools": self.used_tools,
        }


class Agent:
    def __init__(
        self,
        store: SelfModelStore,
        policy: Policy,
        audit: AuditLog,
        tools: dict[str, Tool],
        provider: Provider | None = None,
        max_sensitivity: Sensitivity = Sensitivity.SENSITIVE,
        max_rounds: int = 4,
        regeln: Any = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._audit = audit
        self._tools = tools
        self._provider = provider
        self._regeln = regeln
        """Benannte Dauerregeln, oder None. Ohne sie fragt Icarus wie bisher."""
        self._max_sensitivity = max_sensitivity
        self._max_rounds = max_rounds
        self._history: list[dict[str, Any]] = []
        self._tainted = False
        """Steht fremder Inhalt im Kontext dieser Runde?

        Wird gesetzt, sobald ein Werkzeug Text aus fremder Quelle geliefert hat,
        und beim nächsten Nutzerbeitrag zurückgesetzt. Alles, was danach in
        derselben Runde passiert, gilt als möglicherweise fremdgesteuert.
        """

    @property
    def provider(self) -> Provider | None:
        return self._provider

    @property
    def policy(self) -> Policy:
        return self._policy

    # -- Kontext -----------------------------------------------------------

    def effective_sensitivity_ceiling(self) -> Sensitivity:
        """Die tatsächlich geltende Obergrenze für diesen Zug.

        Der Aufrufer darf die Grenze senken, aber nie über das heben, was der
        Anbieter verdient. Ein externes Modell bekommt ausschließlich `normal`.
        Ein Anbieter auf Loopback darf auch `sensitive` sehen — genau dafür ist
        die lokale Variante da. `special_category` bleibt in beiden Fällen
        zurück und braucht eine eigene, ausdrückliche Freigabe.

        Ohne Anbieter gilt die strengste Grenze, damit ein später gesetzter
        Anbieter nicht versehentlich einen zu weiten Kontext erbt.
        """
        provider_ceiling = Sensitivity.NORMAL
        if self._provider is not None and getattr(self._provider, "is_local", False):
            provider_ceiling = Sensitivity.SENSITIVE
        return _lower_of(self._max_sensitivity, provider_ceiling)

    def assert_egress_allowed(self, messages: list[dict[str, Any]]) -> None:
        """Zweite, unabhängige Prüfung unmittelbar vor dem Versand.

        Der Kontextaufbau filtert bereits. Diese Prüfung traut ihm nicht: sie
        sieht sich die fertige Nutzlast an und vergleicht sie gegen die Aussagen
        im Selbstmodell, die über der Grenze liegen. Damit kann kein anderer
        Codeweg — ein Werkzeugergebnis, ein Verlauf aus einer früheren Runde,
        eine künftige Erweiterung — etwas hinaustragen, das hier nie erlaubt
        war. Fail-closed: im Zweifel Abbruch.
        """
        ceiling = _RANK[self.effective_sensitivity_ceiling()]
        haystack = "\n".join(
            m["content"]
            for m in messages
            if isinstance(m.get("content"), str)
        )
        if not haystack:
            return
        for assertion in self._store.usable():
            if _RANK[assertion.sensitivity] <= ceiling:
                continue
            statement = assertion.statement.strip()
            if len(statement) < 12 or statement not in haystack:
                continue
            self._audit.record(
                tool="egress_guard",
                action_class=ActionClass.OUTWARD.value,
                level=ApprovalLevel.DENY.value,
                outcome="refused",
                arguments={
                    "assertion_id": assertion.id,
                    "sensitivity": assertion.sensitivity.value,
                    "ceiling": self.effective_sensitivity_ceiling().value,
                },
                model=self._provider.model if self._provider else None,
                detail=(
                    "Egress verweigert: eine Aussage über der geltenden "
                    "Schutzbedarfsgrenze stand in der Nutzlast."
                ),
            )
            raise EgressBlocked(
                f"Aussage {assertion.id} ist als "
                f"{assertion.sensitivity.value} markiert und darf nicht an "
                f"diesen Anbieter gehen."
            )

    def context(self) -> str:
        """Baut den Wissensblock über den Nutzer.

        Nur `usable()`-Aussagen: nichts Ersetztes, nichts Abgelaufenes, nichts
        Widerrufenes. Genau hier zahlt sich das Selbstmodell aus — ein flacher
        Faktenspeicher würde „Wohnt in Hamburg" munter mitliefern.

        Zusätzlich greift der Schutzbedarf: was über
        `effective_sensitivity_ceiling()` liegt, wird nur gezählt, nicht
        übermittelt.
        """
        shareable = self._store.shareable(self.effective_sensitivity_ceiling())
        constraints = constraints_from_store(self._store)

        aktuell: list[str] = []
        alt: list[str] = []
        for a in shareable:
            zeile = f"- [{a.kind.value}] {a.statement} ({describe(a)})"
            if judge(a) is Currency.OUTDATED:
                alt.append(zeile)
            else:
                aktuell.append(zeile)

        lines = []
        if aktuell:
            lines.append("Was du über den Nutzer weißt:")
            lines.extend(aktuell)

        if alt:
            # Getrennt statt weggelassen: der Nutzer darf nach seiner eigenen
            # Vergangenheit fragen. Aber diese Zeilen dürfen nie als Gegenwart
            # auftreten, und dafür braucht es die eigene Überschrift.
            lines.append(
                "\nAlte Angaben — nicht als aktuell behaupten, im Zweifel nachfragen:"
            )
            lines.extend(alt)

        # Strittiges kommt mit beiden Seiten und einer eigenen Überschrift.
        # Es wegzulassen wäre bequem und falsch: Der Nutzer hat es gesagt, und
        # das Modell soll nachfragen können statt zu raten. Es unter „was du
        # weißt" zu führen wäre schlimmer — dann wählt das Modell eine Seite.
        streitig = [
            a for a in self._store.disputed()
            if _RANK[a.sensitivity] <= _RANK[self.effective_sensitivity_ceiling()]
        ]
        if streitig:
            lines.append(
                "\nUngeklärt — hier widersprechen sich Angaben. Nichts davon als "
                "Tatsache behaupten; wenn es darauf ankommt, nachfragen:"
            )
            lines.extend(
                f"- [{a.kind.value}] {a.statement} ({describe(a)})" for a in streitig
            )

        withheld = len(self._store.usable()) - len(shareable)
        if withheld == 1:
            lines.append(
                "\n(Eine weitere Aussage ist als besonders geschützt markiert "
                "und wird dir nicht übermittelt.)"
            )
        elif withheld > 1:
            lines.append(
                f"\n({withheld} weitere Aussagen sind als besonders geschützt "
                "markiert und werden dir nicht übermittelt.)"
            )

        if constraints:
            lines.append("\nBindende Grenzen des Nutzers:")
            lines.extend(f"- {c}" for c in constraints)

        return "\n".join(lines) if lines else "Über den Nutzer ist noch nichts bekannt."

    # -- Gesprächsrunde ----------------------------------------------------

    def send(self, message: str) -> Turn:
        if self._provider is None:
            return Turn(
                reply=(
                    "Es ist kein Modell konfiguriert. Das Gedächtnis funktioniert "
                    "trotzdem — Aussagen lassen sich speichern, ansehen und "
                    "widerrufen. Für Gespräche einen Anbieter in .env eintragen."
                )
            )

        # Ein neuer Beitrag des Nutzers ist eine vertrauenswürdige Absicht und
        # hebt die Kontamination der vorigen Runde auf.
        self._tainted = False
        self._history.append({"role": "user", "content": message})
        turn = Turn()
        schemas = [t.schema() for t in self._tools.values()]

        for _ in range(self._max_rounds):
            messages = [
                {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{self.context()}"},
                *self._history,
            ]
            self.assert_egress_allowed(messages)
            try:
                reply = self._provider.complete(messages, schemas)
            except ProviderError as exc:
                turn.reply = f"Das Modell war nicht erreichbar: {exc}"
                return turn

            if not reply.tool_calls:
                turn.reply = reply.text
                self._history.append({"role": "assistant", "content": reply.text})
                return turn

            self._history.append(
                {
                    "role": "assistant",
                    "content": reply.text,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in reply.tool_calls
                    ],
                }
            )

            blocked = False
            for call in reply.tool_calls:
                outcome = self._handle(call, turn)
                self._history.append(
                    {"role": "tool", "tool_call_id": call.id, "content": outcome}
                )
                if turn.approvals:
                    blocked = True

            if blocked:
                # Der Rest der Runde wartet auf den Nutzer.
                turn.reply = reply.text or "Dafür brauche ich deine Freigabe."
                return turn

        turn.reply = turn.reply or "Ich komme hier nicht weiter."
        return turn

    # -- Direkter Werkzeugaufruf ------------------------------------------

    @property
    def tool_names(self) -> list[str]:
        """Welche Werkzeuge es gerade gibt.

        Eine Dauerregel muss ein echtes Werkzeug nennen. Täte sie es nicht,
        würde sie still nie greifen — und der Nutzer glaubte, er habe etwas
        freigegeben, das dann doch jedes Mal nachfragt.
        """
        return sorted(self._tools)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Ruft ein Werkzeug ohne Modell auf — durch dieselbe Policy.

        Gebraucht wird das von der MCP-Tür: Dort stellt ein *fremder* Assistent
        die Anträge, nicht das Modell im Haus. Genau deshalb darf dieser Weg
        keine Abkürzung sein. Er geht durch `_handle()`, also durch Grenzen aus
        dem Selbstmodell, durch die Anhebung nach fremdem Inhalt und durch das
        Audit-Log — dieselben vier Schritte wie im Gespräch.

        Außenwirksames wird hier nicht ausgeführt, sondern als Antrag
        zurückgegeben. Ein Assistent auf der anderen Seite einer Leitung kann
        keine Bestätigung abtippen; das kann nur der Mensch vor der App. Die
        Antwort sagt ihm das, statt still zu scheitern.
        """
        if name not in self._tools:
            return {"ok": False, "text": f"Unbekanntes Werkzeug: {name}", "approvals": []}

        turn = Turn()
        call = ToolCall(id=f"mcp-{uuid.uuid4().hex[:8]}", name=name, arguments=arguments)
        text = self._handle(call, turn)

        if turn.approvals:
            approval = turn.approvals[0]
            text = (
                "Das braucht deine Freigabe in Icarus. Was passieren würde:\n\n"
                f"{approval.dry_run}\n\n"
                f"Antrag {approval.id} liegt in der App unter „Gespräch“."
            )
        return {
            "ok": not turn.approvals,
            "text": text,
            "approvals": [a.to_dict() for a in turn.approvals],
            "notices": turn.notices,
        }

    # -- Werkzeugaufruf ----------------------------------------------------

    def _handle(self, call: ToolCall, turn: Turn) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return f"Unbekanntes Werkzeug: {call.name}"

        model_name = self._provider.model if self._provider else None

        # **Vor** der Freigabe, nicht erst beim Ausführen. Ein Aufruf, dem
        # Pflichtfelder fehlen, darf nie als Antrag vorgelegt werden: der
        # Trockenlauf zeigte dann „An: None / Betreff: None“, und wer die
        # Bestätigungsphrase tippt, gibt etwas frei, das er nie gesehen hat.
        # Genau das ist die Zusage, die der Trockenlauf tragen soll.
        #
        # Kleine Modelle benennen Parameter regelmäßig falsch — `empfaenger`
        # statt `to`. Sie sollen erfahren, wie die Felder heißen, statt den
        # Nutzer mit einem leeren Antrag zu behelligen.
        fehlend = _fehlende_pflichtfelder(tool, call.arguments)
        if fehlend:
            erwartet = ", ".join(tool.parameters.get("properties", {})) or "keine"
            detail = (
                f"Dem Aufruf von {tool.name} fehlt: {', '.join(fehlend)}. "
                f"Erwartete Felder: {erwartet}."
            )
            self._audit.record(
                tool.name, tool.classify(call.arguments).value, "auto", "failed",
                call.arguments, model=model_name, detail=detail,
            )
            return f"Fehlgeschlagen: {detail}"

        # Manche Werkzeuge werden erst durch ihre Argumente außenwirksam.
        action_class = tool.classify(call.arguments)
        # Die engste Regel, die auf genau diesen Aufruf passt. Ob sie greift,
        # entscheidet die Policy — in einer kontaminierten Runde tut sie es nicht.
        regel = None
        if self._regeln is not None:
            try:
                regel = self._regeln.passende(tool.name, call.arguments)
            except Exception:  # noqa: BLE001 - eine kaputte Regelbank darf nichts freigeben
                regel = None

        decision = self._policy.decide(
            tool.name,
            action_class,
            call.arguments,
            constraints_from_store(self._store),
            # Sobald fremder Text im Kontext steht, ist jede folgende Absicht
            # womöglich von dort diktiert. Die Policy hebt dann die Stufe an.
            tainted=self._tainted,
            regel=regel,
        )

        if decision.denied:
            self._audit.record(
                tool.name, decision.action_class.value, decision.level.value,
                "denied", call.arguments, model=model_name,
                detail="; ".join(decision.reasons),
            )
            reason = "; ".join(decision.reasons) or "Durch eine Grenze verboten."
            turn.notices.append(f"Abgelehnt: {tool.name} — {reason}")
            return f"Abgelehnt: {reason}"

        if decision.needs_approval:
            approval = self._policy.request(
                tool.name, call.arguments, decision, tool.dry_run(call.arguments)
            )
            self._audit.record(
                tool.name, decision.action_class.value, decision.level.value,
                "pending", call.arguments, model=model_name, detail=approval.id,
            )
            turn.approvals.append(approval)
            return "Wartet auf Freigabe durch den Nutzer."

        result = self._execute(tool, call.arguments, decision.level, turn, model_name)
        if tool.returns_untrusted:
            self._tainted = True
        return result

    def _execute(
        self,
        tool: Tool,
        arguments: dict[str, Any],
        level: ApprovalLevel,
        turn: Turn,
        model_name: str | None,
        approved_by: str | None = None,
    ) -> str:
        fehlend = _fehlende_pflichtfelder(tool, arguments)
        if fehlend:
            # Vor dem Aufruf prüfen statt den TypeError abzufangen. Sonst steht
            # im Gespräch „build_registry.<locals>.remember() missing 1
            # required positional argument" — ein interner Funktionsname für
            # den Nutzer, und für das Modell kein Signal, womit es den Aufruf
            # reparieren könnte. Kleine Modelle benennen Parameter regelmäßig
            # falsch; sie sollen erfahren, wie die Felder heißen.
            erwartet = ", ".join(tool.parameters.get("properties", {})) or "keine"
            detail = (
                f"Dem Aufruf von {tool.name} fehlt: {', '.join(fehlend)}. "
                f"Erwartete Felder: {erwartet}."
            )
            self._audit.record(
                tool.name, tool.classify(arguments).value, level.value, "failed",
                arguments, model=model_name, detail=detail, approved_by=approved_by,
            )
            return f"Fehlgeschlagen: {detail}"

        try:
            result = tool.run(**arguments)
        except Exception as exc:
            self._audit.record(
                tool.name, tool.classify(arguments).value, level.value, "failed",
                arguments, model=model_name, detail=str(exc), approved_by=approved_by,
            )
            return f"Fehlgeschlagen: {exc}"

        self._audit.record(
            tool.name, tool.classify(arguments).value, level.value, "executed",
            arguments, model=model_name, result=result[:500], approved_by=approved_by,
        )
        turn.used_tools.append(tool.name)
        if level is ApprovalLevel.NOTIFY:
            turn.notices.append(tool.dry_run(arguments))
        return result

    # -- Freigabe einlösen -------------------------------------------------

    def resolve(
        self, approval_id: str, granted: bool, confirmation: str | None = None
    ) -> Turn:
        """Löst eine Freigabe ein und führt die Runde zu Ende.

        Ablehnung ist ein gültiges Ergebnis und liefert eine Antwort. Eine
        *fehlgeschlagene* Freigabe — falsche Bestätigung, abgelaufener oder
        unbekannter Antrag — wirft dagegen einen PolicyError nach oben. Beides
        zu vermischen hieße, der Oberfläche zu signalisieren, alles sei erledigt,
        obwohl der Antrag noch offen ist.
        """
        turn = Turn()

        if not granted:
            approval = self._policy.reject(approval_id)
            self._audit.record(
                approval.tool,
                approval.decision.action_class.value,
                approval.decision.level.value,
                "refused",
                approval.arguments,
                approved_by="user",
                detail="Vom Nutzer abgelehnt.",
            )
            turn.reply = "Abgelehnt. Ich habe nichts ausgeführt."
            return turn

        # Wirft bei falscher Bestätigung; der Antrag bleibt dann bestehen.
        approval = self._policy.grant(approval_id, confirmation)

        tool = self._tools[approval.tool]
        model_name = self._provider.model if self._provider else None
        result = self._execute(
            tool, approval.arguments, approval.decision.level, turn, model_name,
            approved_by="user",
        )

        self._history.append(
            {"role": "user", "content": f"[Freigabe erteilt] Ergebnis: {result}"}
        )
        if self._provider is not None:
            follow_up = self.send("Fasse kurz zusammen, was jetzt passiert ist.")
            turn.reply = follow_up.reply
            turn.approvals = follow_up.approvals
        else:
            turn.reply = result
        turn.used_tools.append(tool.name)
        return turn

    def reset(self) -> None:
        self._history.clear()


def _fehlende_pflichtfelder(tool: Any, arguments: dict[str, Any]) -> list[str]:
    """Welche im Schema geforderten Felder im Aufruf fehlen."""
    erforderlich = tool.parameters.get("required", []) or []
    return [name for name in erforderlich if name not in arguments]


__all__ = ["Agent", "Turn", "SYSTEM_PROMPT"]
