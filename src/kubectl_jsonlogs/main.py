"""
kubectl-jsonlogs: A kubectl plugin to parse and colorize JSON and timestamped logs.
Works as both a kubectl plugin and k9s plugin.

Usage:
    # As kubectl plugin (pipe mode):
    kubectl logs <pod> | kubectl-jsonlogs

    # As k9s plugin (direct mode):
    kubectl-jsonlogs <name> <namespace> <context> [container]

    # When container is not specified, shows logs from all containers

Features:
    - Pretty-prints JSON logs with jq-like colorization
    - Colorizes timestamped non-JSON logs
    - Supports pods view: automatically tails logs from all containers
    - Highlights container names in bold cyan for multi-container pods
    - Minimal dependencies (uses only Python standard library)
"""

import json
import re
import subprocess
import sys
from typing import Optional


# ANSI color codes (no external dependencies)
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'

    # JSON colors (jq-like)
    JSON_KEY = '\033[34m'      # Blue
    JSON_STRING = '\033[32m'   # Green
    JSON_NUMBER = '\033[35m'   # Magenta
    JSON_BOOLEAN = '\033[33m'  # Yellow
    JSON_NULL = '\033[90m'     # Dark gray
    JSON_BRACE = '\033[36m'    # Cyan

    # Log level colors
    ERROR = '\033[31m'         # Red
    WARN = '\033[33m'          # Yellow
    INFO = '\033[36m'          # Cyan
    DEBUG = '\033[90m'         # Dark gray

    # Timestamp color
    TIMESTAMP = '\033[90m'     # Dark gray

    # Pulumi output colors
    PULUMI_RESOURCE_CREATING = '\033[32m'      # Green for creating
    PULUMI_RESOURCE_UPDATING = '\033[33m'      # Yellow for updating
    PULUMI_RESOURCE_CREATED = '\033[32m'       # Green for created
    PULUMI_SECTION_HEADER = '\033[1m\033[36m'  # Bold cyan for section headers
    PULUMI_DURATION = '\033[35m'               # Magenta for duration
    PULUMI_STATUS = '\033[36m'                 # Cyan for status indicators


# ---------------------------------------------------------------------------
# Pulumi output detection and formatting
# ---------------------------------------------------------------------------

def is_pulumi_output(text: str) -> bool:
    """Check if a string contains Pulumi output."""
    if not text:
        return False
    pulumi_patterns = [
        r'pulumi:pulumi:Stack',
        r'Resource Updated',
        r'Updating \(resource\)',
        r'Previewing update',
        r'@ updating',
        r'@ previewing',
        r'Outputs:',
        r'Resources:',
        r'Duration:',
        r'\+\s+\w+:\w+:',
        r'pulumi up failed',
        r'failed to run update',
        r'error: \d+ error occurred',
        r'creating failed',
        r'\*\*failed\*\*',
        r'Diagnostics:',
        r'operation failed',
    ]
    return any(re.search(pattern, text) for pattern in pulumi_patterns)


def format_pulumi_output(text: str) -> str:
    """Format and colorize Pulumi output."""
    lines = text.split('\n')
    formatted_lines = []

    for line in lines:
        if not line.strip():
            formatted_lines.append('')
            continue

        section_match = re.match(r'^(Outputs|Resources|Duration):\s*(.*)', line)
        if section_match:
            section_name = section_match.group(1)
            section_value = section_match.group(2)
            if section_value:
                formatted_lines.append(
                    f"{Colors.PULUMI_SECTION_HEADER}{section_name}:{Colors.RESET} {section_value}"
                )
            else:
                formatted_lines.append(
                    f"{Colors.PULUMI_SECTION_HEADER}{section_name}:{Colors.RESET}"
                )
            continue

        resource_match_with_time = re.match(
            r'^(\s*)([+\-~])\s+([^\s]+(?::[^\s]+)*)\s+(.+?)\s+(\w+)\s+\(([^)]+)\)\s*$', line
        )
        resource_match_no_time = re.match(
            r'^(\s*)([+\-~])\s+([^\s]+(?::[^\s]+)*)\s+(.+?)\s+(\w+)\s*$', line
        )

        if resource_match_with_time:
            indent = resource_match_with_time.group(1)
            symbol = resource_match_with_time.group(2)
            resource_type = resource_match_with_time.group(3)
            resource_name = resource_match_with_time.group(4)
            status = resource_match_with_time.group(5)
            time = resource_match_with_time.group(6)

            if 'failed' in status.lower():
                status_color = Colors.ERROR
            elif 'creating' in status.lower():
                status_color = Colors.PULUMI_RESOURCE_CREATING
            elif 'updating' in status.lower():
                status_color = Colors.PULUMI_RESOURCE_UPDATING
            elif 'created' in status.lower() or 'updated' in status.lower():
                status_color = Colors.PULUMI_RESOURCE_CREATED
            else:
                status_color = Colors.PULUMI_STATUS

            formatted_lines.append(
                f"{indent}{symbol}  {Colors.JSON_BRACE}{resource_type}{Colors.RESET} "
                f"{resource_name} {status_color}{status} ({time}){Colors.RESET}"
            )
            continue
        elif resource_match_no_time:
            indent = resource_match_no_time.group(1)
            symbol = resource_match_no_time.group(2)
            resource_type = resource_match_no_time.group(3)
            resource_name = resource_match_no_time.group(4)
            status = resource_match_no_time.group(5)

            if 'failed' in status.lower():
                status_color = Colors.ERROR
            elif 'create' in status.lower():
                status_color = Colors.PULUMI_RESOURCE_CREATING
            elif 'update' in status.lower():
                status_color = Colors.PULUMI_RESOURCE_UPDATING
            else:
                status_color = Colors.PULUMI_STATUS

            formatted_lines.append(
                f"{indent}{symbol}  {Colors.JSON_BRACE}{resource_type}{Colors.RESET} "
                f"{resource_name} {status_color}{status}{Colors.RESET}"
            )
            continue

        if re.match(r'^@\s+(updating|previewing)', line, re.IGNORECASE):
            formatted_lines.append(f"{Colors.PULUMI_RESOURCE_UPDATING}{line}{Colors.RESET}")
            continue

        output_match = re.match(r'^(\s+)(\w+):\s+(.+)', line)
        if output_match:
            indent = output_match.group(1)
            key = output_match.group(2)
            value = output_match.group(3)
            formatted_lines.append(
                f"{indent}{Colors.JSON_KEY}{key}{Colors.RESET}: {Colors.JSON_STRING}{value}{Colors.RESET}"
            )
            continue

        summary_match = re.match(
            r'^(\s*)([+\-])\s+(\d+)\s+(to\s+)?(created|updated|deleted|create)',
            line, re.IGNORECASE,
        )
        if summary_match:
            indent = summary_match.group(1)
            symbol = summary_match.group(2)
            count = summary_match.group(3)
            to_prefix = summary_match.group(4) or ''
            action = summary_match.group(5)
            formatted_lines.append(
                f"{indent}{symbol} {Colors.JSON_NUMBER}{count}{Colors.RESET} "
                f"{Colors.PULUMI_RESOURCE_CREATED}{to_prefix}{action}{Colors.RESET}"
            )
            continue

        duration_match = re.match(r'^Duration:\s*(.+)', line)
        if duration_match:
            duration = duration_match.group(1)
            formatted_lines.append(
                f"{Colors.PULUMI_SECTION_HEADER}Duration:{Colors.RESET} "
                f"{Colors.PULUMI_DURATION}{duration}{Colors.RESET}"
            )
            continue

        if re.search(
            r'(Resource (Updated|Created|Deleted)|Previewing update|Updating \(resource\))',
            line, re.IGNORECASE,
        ):
            formatted_lines.append(f"{Colors.PULUMI_SECTION_HEADER}{line}{Colors.RESET}")
            continue

        error_match = re.match(r'^(\s*)error:\s+(.+)', line, re.IGNORECASE)
        if error_match:
            indent = error_match.group(1)
            error_msg = error_match.group(2)
            formatted_lines.append(
                f"{indent}{Colors.ERROR}error:{Colors.RESET} {Colors.ERROR}{error_msg}{Colors.RESET}"
            )
            continue

        if re.search(r'\*\*failed\*\*|creating failed|update failed', line, re.IGNORECASE):
            formatted_lines.append(f"{Colors.ERROR}{line}{Colors.RESET}")
            continue

        diagnostics_match = re.match(r'^(\s*)Diagnostics:\s*$', line)
        if diagnostics_match:
            indent = diagnostics_match.group(1)
            formatted_lines.append(f"{indent}{Colors.PULUMI_SECTION_HEADER}Diagnostics:{Colors.RESET}")
            continue

        aws_error_match = re.match(r'^(\s*)(\w+Exception):\s+(.+)', line)
        if aws_error_match:
            indent = aws_error_match.group(1)
            error_type = aws_error_match.group(2)
            error_msg = aws_error_match.group(3)
            formatted_lines.append(
                f"{indent}{Colors.ERROR}{error_type}:{Colors.RESET} {Colors.ERROR}{error_msg}{Colors.RESET}"
            )
            continue

        if re.match(r'^\s+.*\.(go|py|js|ts|java):\d+', line):
            formatted_lines.append(f"{Colors.DEBUG}{line}{Colors.RESET}")
            continue

        operation_failed_match = re.match(
            r'^(\s*)operation failed\s*\((.+)\):\s*(.+)', line, re.IGNORECASE
        )
        if operation_failed_match:
            indent = operation_failed_match.group(1)
            attempt_info = operation_failed_match.group(2)
            error_detail = operation_failed_match.group(3)
            formatted_lines.append(
                f"{indent}{Colors.ERROR}operation failed ({attempt_info}):{Colors.RESET} "
                f"{Colors.ERROR}{error_detail}{Colors.RESET}"
            )
            continue

        formatted_lines.append(line)

    return '\n'.join(formatted_lines)


# ---------------------------------------------------------------------------
# OpenHands agent state message detection and formatting
# ---------------------------------------------------------------------------

# Maximum visible characters shown for string values before truncating.
_MAX_STR_DISPLAY_LEN = 120

# Regex that strips ANSI escape sequences (used for visible-length calculation).
_ANSI_RE = re.compile(r'\033\[[^m]+m')


def _visible_len(s: str) -> int:
    """Return the printable length of *s* (ANSI escape codes excluded)."""
    return len(_ANSI_RE.sub('', s))


def _find_closing_bracket(text: str, open_ch: str, close_ch: str, start: int) -> int:
    """Return the index of the matching closing bracket in *text*.

    Starts just AFTER the opening bracket (position *start*) and respects
    nesting depth and quoted strings.

    Args:
        text: Text to search.
        open_ch: Opening bracket character (e.g. ``'{'``).
        close_ch: Closing bracket character (e.g. ``'}'``).
        start: Index immediately after the opening bracket.

    Returns:
        Index of the matching closing bracket, or ``len(text) - 1`` when not
        found.
    """
    depth = 1
    i = start
    in_sq = in_dq = False
    while i < len(text) and depth > 0:
        c = text[i]
        if in_sq:
            if c == '\\':
                i += 2
                continue
            if c == "'":
                in_sq = False
        elif in_dq:
            if c == '\\':
                i += 2
                continue
            if c == '"':
                in_dq = False
        else:
            if c == "'":
                in_sq = True
            elif c == '"':
                in_dq = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
        i += 1
    return i - 1


def _parse_py_string(text: str, start: int) -> tuple[str, int]:
    """Parse a single- or double-quoted Python string starting at *start*.

    Args:
        text: Full text.
        start: Index of the opening quote character.

    Returns:
        ``(token_including_quotes, index_after_closing_quote)``
    """
    quote = text[start]
    i = start + 1
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == quote:
            return text[start:i + 1], i + 1
        i += 1
    return text[start:], len(text)


def _parse_py_token(text: str, start: int) -> tuple[str, int]:
    """Parse a single Python repr token starting at *start*.

    Recognises dicts ``{}``, lists ``[]``, tuples ``()``, single/double-quoted
    strings, ``<Enum>`` reprs, and bare identifiers/numbers.

    Args:
        text: Full text.
        start: Starting index (leading whitespace is skipped).

    Returns:
        ``(token_str, index_after_token)``
    """
    while start < len(text) and text[start] in ' \t\n':
        start += 1
    if start >= len(text):
        return '', start

    c = text[start]

    if c in ("'", '"'):
        return _parse_py_string(text, start)

    if c == '{':
        end = _find_closing_bracket(text, '{', '}', start + 1)
        return text[start:end + 1], end + 1

    if c == '[':
        end = _find_closing_bracket(text, '[', ']', start + 1)
        return text[start:end + 1], end + 1

    if c == '(':
        end = _find_closing_bracket(text, '(', ')', start + 1)
        return text[start:end + 1], end + 1

    if c == '<':
        end = text.find('>', start)
        if end == -1:
            end = len(text) - 1
        return text[start:end + 1], end + 1

    # Bare identifier / number — read until a delimiter OR an opening paren.
    # Stop at '(' so we can detect function-call syntax like UUID(...),
    # datetime.datetime(...), etc.
    i = start
    while i < len(text) and text[i] not in ('(', ',', ' ', '\t', '\n', '}', ']', ')'):
        i += 1

    if i < len(text) and text[i] == '(':
        # Function call: read the whole parenthesised argument list as one token.
        end = _find_closing_bracket(text, '(', ')', i + 1)
        return text[start:end + 1], end + 1

    return text[start:i], i


def _format_py_repr(text: str, level: int = 0, max_str_len: int = _MAX_STR_DISPLAY_LEN) -> str:
    """Format a Python repr value with ANSI colours and indentation.

    Recursively parses ``dict``, ``list``, and ``tuple`` structures, renders
    each key-value pair on its own indented line, and truncates string values
    that exceed *max_str_len* visible characters.

    Args:
        text: Python repr string to format.
        level: Current nesting depth (controls indentation).
        max_str_len: Maximum visible characters for string values.

    Returns:
        ANSI-colorized, indented string.
    """
    text = text.strip()
    if not text:
        return ''

    pad = '  ' * level
    inner_pad = '  ' * (level + 1)

    # ── dict ──────────────────────────────────────────────────────────────
    if text.startswith('{') and text.endswith('}'):
        inner = text[1:-1].strip()
        if not inner:
            return f"{Colors.JSON_BRACE}{{}}{Colors.RESET}"

        pairs: list[tuple[str, str]] = []
        pos = 0
        while pos < len(inner):
            while pos < len(inner) and inner[pos] in ' \t\n,':
                pos += 1
            if pos >= len(inner):
                break
            key_tok, pos = _parse_py_token(inner, pos)
            if not key_tok:
                break
            while pos < len(inner) and inner[pos] in ' \t':
                pos += 1
            if pos < len(inner) and inner[pos] == ':':
                pos += 1
            val_tok, pos = _parse_py_token(inner, pos)
            pairs.append((key_tok, val_tok))

        if not pairs:
            return (
                f"{Colors.JSON_BRACE}{{{Colors.RESET}"
                f"{_colorize_flat(inner)}"
                f"{Colors.JSON_BRACE}}}{Colors.RESET}"
            )

        lines = [f"{Colors.JSON_BRACE}{{{Colors.RESET}"]
        for i, (k, v) in enumerate(pairs):
            comma = ',' if i < len(pairs) - 1 else ''
            colored_k = f"{Colors.JSON_KEY}{k}{Colors.RESET}"
            colored_v = _format_py_repr(v, level + 1, max_str_len)
            lines.append(f"{inner_pad}{colored_k}: {colored_v}{comma}")
        lines.append(f"{pad}{Colors.JSON_BRACE}}}{Colors.RESET}")
        return '\n'.join(lines)

    # ── list / tuple ───────────────────────────────────────────────────────
    if (text.startswith('[') and text.endswith(']')) or (
        text.startswith('(') and text.endswith(')')
    ):
        open_b, close_b = text[0], text[-1]
        inner = text[1:-1].strip()
        if not inner:
            return f"{Colors.JSON_BRACE}{open_b}{close_b}{Colors.RESET}"

        items: list[str] = []
        pos = 0
        while pos < len(inner):
            while pos < len(inner) and inner[pos] in ' \t\n,':
                pos += 1
            if pos >= len(inner):
                break
            item_tok, pos = _parse_py_token(inner, pos)
            if item_tok:
                items.append(item_tok)

        if not items:
            return f"{Colors.JSON_BRACE}{open_b}{close_b}{Colors.RESET}"

        formatted = [_format_py_repr(item, level + 1, max_str_len) for item in items]

        # Render inline when all items are short single-line strings.
        if (
            not any('\n' in f for f in formatted)
            and sum(_visible_len(f) for f in formatted) + len(items) * 2 < 100
        ):
            joined = f"{Colors.JSON_BRACE},{Colors.RESET} ".join(formatted)
            return f"{Colors.JSON_BRACE}{open_b}{Colors.RESET}{joined}{Colors.JSON_BRACE}{close_b}{Colors.RESET}"

        lines = [f"{Colors.JSON_BRACE}{open_b}{Colors.RESET}"]
        for i, f in enumerate(formatted):
            comma = ',' if i < len(formatted) - 1 else ''
            lines.append(f"{inner_pad}{f}{comma}")
        lines.append(f"{pad}{Colors.JSON_BRACE}{close_b}{Colors.RESET}")
        return '\n'.join(lines)

    # ── special objects ────────────────────────────────────────────────────
    if text.startswith('UUID('):
        return f"{Colors.JSON_NUMBER}{text}{Colors.RESET}"

    if text.startswith('<') and text.endswith('>'):
        return f"{Colors.JSON_BOOLEAN}{text}{Colors.RESET}"

    if text == 'None':
        return f"{Colors.JSON_NULL}None{Colors.RESET}"

    if text in ('True', 'False'):
        return f"{Colors.JSON_BOOLEAN}{text}{Colors.RESET}"

    if text.startswith('datetime.'):
        return f"{Colors.JSON_NUMBER}{text}{Colors.RESET}"

    # ── quoted string ──────────────────────────────────────────────────────
    if len(text) >= 2 and text[0] in ("'", '"') and text[-1] == text[0]:
        inner = text[1:-1]
        if len(inner) > max_str_len:
            remaining = len(inner) - max_str_len
            trunc = inner[:max_str_len]
            return (
                f"{Colors.JSON_STRING}{text[0]}{trunc}"
                f"{Colors.RESET}{Colors.DEBUG}[+{remaining}c]"
                f"{Colors.RESET}{Colors.JSON_STRING}{text[0]}{Colors.RESET}"
            )
        return f"{Colors.JSON_STRING}{text}{Colors.RESET}"

    # ── number ─────────────────────────────────────────────────────────────
    if re.match(r'^-?\d+(?:\.\d+)?$', text):
        return f"{Colors.JSON_NUMBER}{text}{Colors.RESET}"

    # ── fallback: flat token colorization ──────────────────────────────────
    return _colorize_flat(text)


# Simple flat token colorizer (used as fallback and for the general multi-line handler).
_OPENHANDS_TOKEN_PATTERN = re.compile(
    r"(UUID\('[0-9a-f-]+'\))"                             # UUID('...')
    r"|(<[A-Za-z][A-Za-z0-9_]*\.[A-Z_]+:\s*'[^']*'>)"   # <EnumClass.MEMBER: 'value'>
    r"|(\bNone\b)"                                         # None
    r"|(\bTrue\b|\bFalse\b)"                               # True / False
    r"|('[^']*'(?=\s*:))"                                  # dict key 'key':
    r"|('[^']*')"                                          # string value 'value'
    r"|(\b\d+(?:\.\d+)?\b)"                                # number
)


def _colorize_flat_token(m: re.Match) -> str:
    """Replace a matched Python repr token with its ANSI-coloured version."""
    if m.group(1):
        return f"{Colors.JSON_NUMBER}{m.group(1)}{Colors.RESET}"
    elif m.group(2):
        return f"{Colors.JSON_BOOLEAN}{m.group(2)}{Colors.RESET}"
    elif m.group(3):
        return f"{Colors.JSON_NULL}None{Colors.RESET}"
    elif m.group(4):
        return f"{Colors.JSON_BOOLEAN}{m.group(4)}{Colors.RESET}"
    elif m.group(5):
        return f"{Colors.JSON_KEY}{m.group(5)}{Colors.RESET}"
    elif m.group(6):
        return f"{Colors.JSON_STRING}{m.group(6)}{Colors.RESET}"
    elif m.group(7):
        return f"{Colors.JSON_NUMBER}{m.group(7)}{Colors.RESET}"
    return m.group(0)


def _colorize_flat(text: str) -> str:
    """Inline token colorizer for Python repr text (no structural formatting)."""
    return _OPENHANDS_TOKEN_PATTERN.sub(_colorize_flat_token, text)


# Keep the old public name as an alias for use in the general multi-line handler.
colorize_python_dict_repr = _colorize_flat


def is_openhands_state_message(text: str) -> bool:
    """Check if a string is an OpenHands agent state/conversation message.

    Detects the multi-line messages logged at conversation creation that
    contain Python repr of OpenHands ``State`` and ``Agent`` objects.

    Args:
        text: String to inspect.

    Returns:
        True when the string matches an OpenHands state message.
    """
    if not text or '\n' not in text:
        return False
    openhands_patterns = [
        r"State:\s*\{",
        r"Agent:\s*\{",
        r"UUID\('[0-9a-f-]+'\)",
        r"<ConversationExecutionStatus\.",
    ]
    return any(re.search(pattern, text) for pattern in openhands_patterns)


def format_openhands_state_message(text: str) -> str:
    """Format and colorize an OpenHands agent state/conversation message.

    Parses messages that contain a plain-text header followed by ``State:``
    and ``Agent:`` sections whose values are Python repr of OpenHands
    objects.  Each section label is highlighted in bold cyan; the dict/list
    repr is rendered with :func:`_format_py_repr` (structured, indented,
    truncated strings).

    Args:
        text: Multi-line message string to format.

    Returns:
        ANSI-colored formatted string.
    """
    lines = text.split('\n')
    formatted_lines = []

    uuid_pattern = re.compile(
        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    )

    for idx, line in enumerate(lines):
        if not line.strip():
            formatted_lines.append('')
            continue

        section_match = re.match(r'^(State|Agent):\s*(.*)', line, re.DOTALL)
        if section_match:
            label = section_match.group(1)
            content = section_match.group(2).strip()
            colored_label = f"{Colors.BOLD}{Colors.JSON_BRACE}{label}:{Colors.RESET}"
            colored_content = _format_py_repr(content, level=1)
            formatted_lines.append(f"{colored_label} {colored_content}")
        elif idx == 0:
            colored_line = uuid_pattern.sub(
                lambda m: f"{Colors.JSON_NUMBER}{m.group(1)}{Colors.RESET}",
                line,
            )
            formatted_lines.append(f"{Colors.BOLD}{colored_line}{Colors.RESET}")
        else:
            formatted_lines.append(_format_py_repr(line.strip(), level=0))

    return '\n'.join(formatted_lines)


# ---------------------------------------------------------------------------
# Core JSON colorization
# ---------------------------------------------------------------------------

def colorize_json_string(json_str: str, indent: int = 2) -> str:
    """Colorize a JSON string similar to jq output."""
    try:
        obj = json.loads(json_str)
        return colorize_json_value(obj, indent=indent, level=0)
    except json.JSONDecodeError:
        return json_str


def colorize_json_value(
    value,
    indent: int = 2,
    level: int = 0,
    key_name: Optional[str] = None,
) -> str:
    """Recursively colorize JSON values."""
    if value is None:
        return f"{Colors.JSON_NULL}null{Colors.RESET}"
    elif isinstance(value, bool):
        return f"{Colors.JSON_BOOLEAN}{str(value).lower()}{Colors.RESET}"
    elif isinstance(value, (int, float)):
        return f"{Colors.JSON_NUMBER}{value}{Colors.RESET}"
    elif isinstance(value, str):
        escaped = json.dumps(value)
        return f"{Colors.JSON_STRING}{escaped}{Colors.RESET}"
    elif isinstance(value, dict):
        if not value:
            return f"{Colors.JSON_BRACE}{{}}{Colors.RESET}"

        lines = [f"{Colors.JSON_BRACE}{{{Colors.RESET}"]
        items = list(value.items())
        for i, (k, v) in enumerate(items):
            key_part = f"{Colors.JSON_KEY}{json.dumps(str(k))}{Colors.RESET}"
            indent_str = " " * ((level + 1) * indent)
            comma = "," if i < len(items) - 1 else ""

            if str(k) in ('msg', 'output', 'error') and isinstance(v, str) and is_pulumi_output(v):
                formatted_pulumi = format_pulumi_output(v)
                pulumi_lines = formatted_pulumi.split('\n')
                if len(pulumi_lines) > 1:
                    lines.append(f"{indent_str}{key_part}:")
                    last_non_empty_idx = -1
                    for pulumi_line in pulumi_lines:
                        lines.append(f"{indent_str}  {pulumi_line}")
                        if pulumi_line.strip():
                            last_non_empty_idx = len(lines) - 1
                    if comma and last_non_empty_idx >= 0:
                        lines[last_non_empty_idx] = lines[last_non_empty_idx] + comma
                    continue
            elif str(k) in ('msg', 'message') and isinstance(v, str) and is_openhands_state_message(v):
                formatted_oh = format_openhands_state_message(v)
                oh_lines = formatted_oh.split('\n')
                if len(oh_lines) > 1:
                    lines.append(f"{indent_str}{key_part}:")
                    last_non_empty_idx = -1
                    for oh_line in oh_lines:
                        lines.append(f"{indent_str}  {oh_line}")
                        if oh_line.strip():
                            last_non_empty_idx = len(lines) - 1
                    if comma and last_non_empty_idx >= 0:
                        lines[last_non_empty_idx] = lines[last_non_empty_idx] + comma
                    continue
            elif (
                isinstance(v, str)
                and '\n' in v
                and str(k) in ('event', 'msg', 'message', 'output', 'error')
            ):
                # General multi-line string: render each line separately instead of
                # showing a single escaped JSON string with literal \n sequences.
                ml_lines = v.split('\n')
                lines.append(f"{indent_str}{key_part}:")
                last_non_empty_idx = -1
                for ml_line in ml_lines:
                    colored_ml = f"{Colors.JSON_STRING}{ml_line}{Colors.RESET}"
                    lines.append(f"{indent_str}  {colored_ml}")
                    if ml_line.strip():
                        last_non_empty_idx = len(lines) - 1
                if comma and last_non_empty_idx >= 0:
                    lines[last_non_empty_idx] = lines[last_non_empty_idx] + comma
                continue

            value_part = colorize_json_value(v, indent, level + 1, key_name=str(k))
            lines.append(f"{indent_str}{key_part}: {value_part}{comma}")
        lines.append(f"{' ' * (level * indent)}{Colors.JSON_BRACE}}}{Colors.RESET}")
        return "\n".join(lines)
    elif isinstance(value, list):
        if not value:
            return f"{Colors.JSON_BRACE}[]{Colors.RESET}"

        lines = [f"{Colors.JSON_BRACE}[{Colors.RESET}"]
        for i, item in enumerate(value):
            value_part = colorize_json_value(item, indent, level + 1, key_name=None)
            comma = "," if i < len(value) - 1 else ""
            indent_str = " " * ((level + 1) * indent)
            lines.append(f"{indent_str}{value_part}{comma}")
        lines.append(f"{' ' * (level * indent)}{Colors.JSON_BRACE}]{Colors.RESET}")
        return "\n".join(lines)
    else:
        return str(value)


# ---------------------------------------------------------------------------
# Plain-text / timestamped line colorization
# ---------------------------------------------------------------------------

def detect_log_level(line: str) -> Optional[str]:
    """Detect log level in a line (case-insensitive)."""
    line_lower = line.lower()
    if re.search(r'\b(error|err|exception|fatal|critical)\b', line_lower):
        return 'ERROR'
    elif re.search(r'\b(warn|warning)\b', line_lower):
        return 'WARN'
    elif re.search(r'\b(debug|trace)\b', line_lower):
        return 'DEBUG'
    elif re.search(r'\b(info|information)\b', line_lower):
        return 'INFO'
    return None


def colorize_timestamped_line(line: str) -> str:
    """Colorize a timestamped log line."""
    colored_line = line

    container_prefix_pattern = r'^(\[[^\]]+\])\s+'
    container_match = re.match(container_prefix_pattern, line)
    if container_match:
        container_prefix = container_match.group(1)
        colored_prefix = f"{Colors.BOLD}{Colors.JSON_BRACE}{container_prefix}{Colors.RESET} "
        colored_line = colored_prefix + line[len(container_prefix):].lstrip()

    timestamp_patterns = [
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)',
        r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})',
        r'(\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\])',
        r'(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})',
    ]

    for pattern in timestamp_patterns:
        match = re.search(pattern, colored_line)
        if match:
            timestamp = match.group(1) if match.lastindex else match.group(0)
            colored_timestamp = f"{Colors.TIMESTAMP}{timestamp}{Colors.RESET}"
            colored_line = colored_line.replace(timestamp, colored_timestamp, 1)
            break

    log_level = detect_log_level(line)
    if log_level == 'ERROR':
        colored_line = f"{Colors.ERROR}{colored_line}{Colors.RESET}"
    elif log_level == 'WARN':
        colored_line = f"{Colors.WARN}{colored_line}{Colors.RESET}"
    elif log_level == 'INFO':
        colored_line = f"{Colors.INFO}{colored_line}{Colors.RESET}"
    elif log_level == 'DEBUG':
        colored_line = f"{Colors.DEBUG}{colored_line}{Colors.RESET}"

    return colored_line


# ---------------------------------------------------------------------------
# Line processor and kubectl runner
# ---------------------------------------------------------------------------

_ANSI_ESCAPE_START = re.compile(r'^\033\[')


def process_line(line: str) -> str:
    """Process a single log line — try JSON first, then timestamped log."""
    line = line.rstrip('\n\r')

    # Lines that already carry ANSI escape codes (e.g. LiteLLM debug output)
    # are passed through unchanged to avoid double-colouring.
    if _ANSI_ESCAPE_START.match(line):
        return line

    container_prefix = None
    container_prefix_pattern = r'^(\[[^\]]+\])\s+'
    container_match = re.match(container_prefix_pattern, line)
    if container_match:
        container_prefix = container_match.group(1)
        line_without_prefix = line[len(container_prefix):].lstrip()
    else:
        line_without_prefix = line

    # Plain-text OpenHands section lines ("State: {...}" / "Agent: {...}") take
    # priority over JSON matching because the Python repr dict often contains
    # {} empty dicts that satisfy json.loads and cause the wrong branch to run.
    section_match = re.match(r'^(State|Agent):\s*(\{.*)', line_without_prefix)
    if section_match:
        label = section_match.group(1)
        content = section_match.group(2).strip()
        colored_label = f"{Colors.BOLD}{Colors.JSON_BRACE}{label}:{Colors.RESET}"
        colored_content = _format_py_repr(content, level=1)
        result = f"{colored_label}\n{colored_content}"
        if container_prefix:
            colored_prefix = f"{Colors.BOLD}{Colors.JSON_BRACE}{container_prefix}{Colors.RESET} "
            return colored_prefix + result
        return result

    try:
        json.loads(line_without_prefix)
        colored_json = colorize_json_string(line_without_prefix)
        if container_prefix:
            colored_prefix = f"{Colors.BOLD}{Colors.JSON_BRACE}{container_prefix}{Colors.RESET} "
            return colored_prefix + colored_json
        return colored_json
    except json.JSONDecodeError:
        pass

    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.finditer(json_pattern, line_without_prefix)

    if matches:
        result = line_without_prefix
        offset = 0
        for match in matches:
            json_str = match.group(0)
            try:
                json.loads(json_str)
                colored_json = colorize_json_string(json_str)
                start, end = match.span()
                result = result[:start + offset] + colored_json + result[end + offset:]
                offset += len(colored_json) - len(json_str)
            except json.JSONDecodeError:
                continue

        if result != line_without_prefix:
            parts = re.split(json_pattern, result)
            colored_parts = [
                colorize_timestamped_line(part) if part.strip() else part
                for part in parts
            ]
            colored_result = ''.join(colored_parts)
            if container_prefix:
                colored_prefix = f"{Colors.BOLD}{Colors.JSON_BRACE}{container_prefix}{Colors.RESET} "
                return colored_prefix + colored_result
            return colored_result

    return colorize_timestamped_line(line)


def execute_kubectl_logs(
    pod_name: str,
    namespace: str,
    context: str,
    container: Optional[str] = None,
) -> int:
    """Execute ``kubectl logs`` and stream colorized output.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        context: Kubernetes context.
        container: Container name; when omitted all containers are tailed.

    Returns:
        kubectl exit code.
    """
    cmd = ['kubectl', 'logs']

    if namespace:
        cmd.extend(['-n', namespace])
    if context:
        cmd.extend(['--context', context])

    if container:
        cmd.extend(['-c', container])
    else:
        cmd.append('--all-containers')

    cmd.extend(['-f', pod_name])

    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
        )
        for line in process.stdout:
            try:
                print(process_line(line))
            except Exception:
                print(line, end='')
            sys.stdout.flush()
        process.wait()
        return process.returncode or 0
    except KeyboardInterrupt:
        if process:
            process.terminate()
        sys.exit(0)
    except Exception as exc:
        print(f"Error executing kubectl logs: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point for the ``kubectl-jsonlogs`` command."""
    # k9s direct mode: kubectl-jsonlogs <pod> <namespace> <context> [container]
    # In direct mode we always keep colors because output is piped to `less -R`.
    # Only strip colors in pipe mode (stdin → stdout) when stdout is not a TTY.
    if len(sys.argv) >= 4:
        pod_name = sys.argv[1]
        namespace = sys.argv[2]
        context = sys.argv[3]
        container = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
        sys.exit(execute_kubectl_logs(pod_name, namespace, context, container))

    # Pipe mode: kubectl logs <pod> | kubectl-jsonlogs
    # Strip colors when stdout is not a TTY (e.g. redirected to a file).
    if not sys.stdout.isatty():
        for attr in dir(Colors):
            if not attr.startswith('_'):
                setattr(Colors, attr, '')

    try:
        for line in sys.stdin:
            try:
                print(process_line(line))
            except Exception:
                print(line, end='')
    except KeyboardInterrupt:
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)


if __name__ == '__main__':
    main()
