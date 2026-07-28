"""Domain errors exposed by mini-cmux."""


class MiniCmuxError(RuntimeError):
    """A user-facing mini-cmux error."""


class TmuxError(MiniCmuxError):
    """A failed tmux operation."""

