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
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog
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

SYSTEM_PROMPT = """Du bist Icarus, ein persönlicher Assistent mit langfristigem Gedächtnis.

Über den Nutzer weißt du nur, was unten steht. Rate nichts dazu.

Regeln:
- Nutze `merken` nur, wenn der Nutzer etwas über sich mitteilt, das über das
  aktuelle Gespräch hinaus gilt. Beiläufiges gehört nicht ins Gedächtnis.
- Für Fakten, die sich geändert haben können, und für alles Aktuelle nutze
  Werkzeuge statt zu raten. Das gilt besonders für Datum und Uhrzeit.
- Grenzen des Nutzers sind bindend. Erkläre, wenn etwas daran scheitert.
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
    ) -> None:
        self._store = store
        self._policy = policy
        self._audit = audit
        self._tools = tools
        self._provider = provider
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

    def context(self) -> str:
        """Baut den Wissensblock über den Nutzer.

        Nur `usable()`-Aussagen: nichts Ersetztes, nichts Abgelaufenes, nichts
        Widerrufenes. Genau hier zahlt sich das Selbstmodell aus — ein flacher
        Faktenspeicher würde „Wohnt in Hamburg" munter mitliefern.

        Zusätzlich greift der Schutzbedarf: `special_category` geht per Default
        nicht an ein externes Modell.
        """
        shareable = self._store.shareable(self._max_sensitivity)
        constraints = constraints_from_store(self._store)

        lines = []
        if shareable:
            lines.append("Was du über den Nutzer weißt:")
            for a in shareable:
                mark = " (selbst gefolgert)" if a.provenance.source_type.value == "inference" else ""
                lines.append(f"- [{a.kind.value}] {a.statement}{mark}")

        withheld = len(self._store.usable()) - len(shareable)
        if withheld > 0:
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

    # -- Werkzeugaufruf ----------------------------------------------------

    def _handle(self, call: ToolCall, turn: Turn) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return f"Unbekanntes Werkzeug: {call.name}"

        model_name = self._provider.model if self._provider else None
        # Manche Werkzeuge werden erst durch ihre Argumente außenwirksam.
        action_class = tool.classify(call.arguments)
        decision = self._policy.decide(
            tool.name,
            action_class,
            call.arguments,
            constraints_from_store(self._store),
            # Sobald fremder Text im Kontext steht, ist jede folgende Absicht
            # womöglich von dort diktiert. Die Policy hebt dann die Stufe an.
            tainted=self._tainted,
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


__all__ = ["Agent", "Turn", "SYSTEM_PROMPT"]
