"""Grading data layer (task 012): session loading, the label store and graded results.

No Qt and no vision internals: this reads only the decision-log JSONL and the session folder.
The Qt tool is ``hwsorter.gui.grading``; the report (task 013) consumes ``LabelStore`` and
``grade_session``. The label-store JSON schema is documented in ``labels``.
"""

from .grade import GradedGroup, GradeReport, GradingSession, grade_session
from .labels import (
    KIND_CLASS,
    KIND_EXCLUDED,
    KIND_MULTI,
    KIND_OUT_OF_SCOPE,
    LABEL_SCHEMA_VERSION,
    Label,
    LabelStore,
    Rematch,
)
from .session_log import LoggedGroup, LoggedPart, SessionData, load_session

__all__ = [
    "KIND_CLASS",
    "KIND_EXCLUDED",
    "KIND_MULTI",
    "KIND_OUT_OF_SCOPE",
    "LABEL_SCHEMA_VERSION",
    "GradeReport",
    "GradedGroup",
    "GradingSession",
    "Label",
    "LabelStore",
    "LoggedGroup",
    "LoggedPart",
    "Rematch",
    "SessionData",
    "grade_session",
    "load_session",
]
