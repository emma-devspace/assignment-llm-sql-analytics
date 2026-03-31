from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 3

FOLLOW_UP_INDICATORS = re.compile(
    r"\b(it|that|those|this|them|the same|previous|above|instead|also|what about|how about|now)\b",
    re.IGNORECASE,
)


@dataclass
class ConversationTurn:
    question: str
    sql: str | None
    answer: str
    status: str


@dataclass
class ConversationSession:
    session_id: str
    turns: list[ConversationTurn] = field(default_factory=list)

    def add_turn(self, turn: ConversationTurn) -> None:
        self.turns.append(turn)
        if len(self.turns) > MAX_HISTORY_TURNS:
            self.turns = self.turns[-MAX_HISTORY_TURNS:]

    def get_context_for_prompt(self) -> str | None:
        if not self.turns:
            return None
        lines = ["Previous conversation:"]
        for i, t in enumerate(self.turns, 1):
            lines.append(f"Q{i}: {t.question}")
            if t.sql:
                lines.append(f"SQL{i}: {t.sql}")
            lines.append(f"A{i}: {t.answer[:200]}")
        return "\n".join(lines)


class ConversationManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}

    def get_or_create_session(self, session_id: str) -> ConversationSession:
        if session_id not in self._sessions:
            self._sessions[session_id] = ConversationSession(session_id=session_id)
        return self._sessions[session_id]

    def is_follow_up(self, question: str, session: ConversationSession) -> bool:
        if not session.turns:
            return False
        word_count = len(question.split())
        if word_count < 8 and FOLLOW_UP_INDICATORS.search(question):
            return True
        if word_count < 5:
            return True
        return False

    def resolve_question(self, question: str, session_id: str) -> tuple[str, str | None]:
        """Returns (question, conversation_context) — context is None for non-follow-ups."""
        session = self.get_or_create_session(session_id)
        if self.is_follow_up(question, session):
            context = session.get_context_for_prompt()
            logger.info(
                "Follow-up detected for session %s, injecting %d turns of context",
                session_id,
                len(session.turns),
            )
            return question, context
        return question, None

    def record_turn(self, session_id: str, question: str, sql: str | None, answer: str, status: str) -> None:
        session = self.get_or_create_session(session_id)
        session.add_turn(
            ConversationTurn(
                question=question,
                sql=sql,
                answer=answer,
                status=status,
            )
        )
