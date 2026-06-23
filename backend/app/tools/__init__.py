"""Tool registry assembly.

Exposes :func:`build_default_tools`, the single place that enumerates every tool
the assistant can call. The names here MUST match ``PROTOCOL.md`` exactly.
"""

from __future__ import annotations

from app.tools.base import Tool
from app.tools.camera import SetCameraZoomTool
from app.tools.device import (
    EndSessionTool,
    MuteMicTool,
    RotateCameraTool,
    SetCameraTool,
)
from app.tools.email_read import ReadEmailsTool, ReadEmailTool
from app.tools.email_send import SendEmailTool
from app.tools.identify import IdentifyImageTool
from app.tools.location import GetLocationTool
from app.tools.messaging import SendMessageTool
from app.tools.notes import CreateNoteTool
from app.tools.recall import ListNotesTool, ListTasksTool
from app.tools.task_manage import (
    CompleteTaskTool,
    DeleteNoteTool,
    DeleteTaskTool,
    UpdateTaskTool,
)
from app.tools.tasks import CreateTaskTool
from app.tools.web_search import WebSearchTool

__all__ = [
    "CompleteTaskTool",
    "CreateNoteTool",
    "CreateTaskTool",
    "DeleteNoteTool",
    "DeleteTaskTool",
    "EndSessionTool",
    "IdentifyImageTool",
    "ListNotesTool",
    "ListTasksTool",
    "GetLocationTool",
    "MuteMicTool",
    "ReadEmailTool",
    "ReadEmailsTool",
    "RotateCameraTool",
    "SendEmailTool",
    "SendMessageTool",
    "SetCameraTool",
    "SetCameraZoomTool",
    "UpdateTaskTool",
    "WebSearchTool",
    "build_default_tools",
]


def build_default_tools() -> list[Tool]:
    """Return fresh instances of every registered tool.

    To add a tool: implement :class:`~app.tools.base.Tool`, import it here, and
    append an instance to this list. It is then automatically schema-exported to
    the model and dispatchable by the engine.
    """
    return [
        CreateNoteTool(),
        WebSearchTool(),
        CreateTaskTool(),
        SendMessageTool(),
        SetCameraZoomTool(),
        ListNotesTool(),
        ListTasksTool(),
        CompleteTaskTool(),
        UpdateTaskTool(),
        DeleteTaskTool(),
        DeleteNoteTool(),
        MuteMicTool(),
        SetCameraTool(),
        RotateCameraTool(),
        EndSessionTool(),
        ReadEmailsTool(),
        ReadEmailTool(),
        SendEmailTool(),
        GetLocationTool(),
        IdentifyImageTool(),
    ]
