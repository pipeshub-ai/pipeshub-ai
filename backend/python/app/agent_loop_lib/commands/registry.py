from __future__ import annotations

from app.agent_loop_lib.commands.base import ARGUMENTS_PLACEHOLDER, Command
from app.agent_loop_lib.core.exceptions import RegistryError

"""Holds loaded Commands and expands one into the text that becomes an agent
goal — the CLI's `/name args` dispatch (see cli.py) is the primary consumer,
but `render()` has no CLI dependency so any host can reuse this."""


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def resolve(self, name: str) -> Command:
        if name not in self._commands:
            raise RegistryError(f"Command {name!r} is not registered")
        return self._commands[name]

    def has(self, name: str) -> bool:
        return name in self._commands

    def names(self) -> list[str]:
        return list(self._commands.keys())

    def overview(self) -> list[dict[str, str]]:
        return [{"name": c.name, "description": c.description} for c in self._commands.values()]

    def render(self, name: str, args: str = "") -> str:
        """Expand a command's body: substitute every `$ARGUMENTS` with
        `args`, or — for a command with no placeholder — append `args` as a
        trailing line so extra text the user typed after `/name` isn't
        silently dropped."""
        command = self.resolve(name)
        if ARGUMENTS_PLACEHOLDER in command.body:
            return command.body.replace(ARGUMENTS_PLACEHOLDER, args)
        if args:
            return f"{command.body}\n\n{args}"
        return command.body

    def load_dir(self, root: str) -> int:
        from app.agent_loop_lib.commands.loader import load_commands_from_dir
        loaded = load_commands_from_dir(root)
        for command in loaded:
            self.register(command)
        return len(loaded)
