"""Texto visible en chats: quita artefactos markdown típicos (**negrita**, __subrayado__)."""


def strip_markdown_display_symbols(s: str) -> str:
    if not s:
        return s
    return s.replace("**", "").replace("__", "")
