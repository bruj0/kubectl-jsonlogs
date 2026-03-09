# kubectl-jsonlogs Plugin

kubectl plugin to parse and colorize JSON and timestamped logs, similar to `jq` but optimized for Kubernetes log streams.

- Works as both a kubectl plugin and k9s plugin (container scope)
- Parses Python `repr` payloads from OpenHands / agent-runner jobs (UUID, Enum, nested dicts)

![Screenshot](./Screenshot%202025-12-09%20at%2010.54.42.png)

## Features

- **JSON Log Parsing**: Pretty-prints JSON logs with colorized output (similar to `jq`)
- **Python `repr` Parsing**: Detects and formats OpenHands agent `State:` / `Agent:` blocks — handles `UUID(...)`, `<Enum.VALUE: 'value'>`, nested dicts/lists, and truncates long strings
- **Multi-line String Rendering**: Displays multi-line values (e.g. `event`, context summaries) as indented blocks instead of escaped `\n`
- **Pulumi Output Formatting**: Automatically detects and pretty-prints Pulumi output in `msg` fields with colorized resource status, outputs, and summaries
- **Timestamped Logs**: Automatically detects and colorizes timestamps in non-JSON logs
- **Log Level Detection**: Colorizes log lines based on detected log levels (ERROR, WARN, INFO, DEBUG)

## Installation

### uv (Recommended)

```bash
uv tool install git+https://github.com/rodrigoleven/kubectl-jsonlogs
```

Or install from a local clone:

```bash
git clone https://github.com/rodrigoleven/kubectl-jsonlogs
uv tool install ./kubectl-jsonlogs
```

Verify:

```bash
kubectl jsonlogs --help
```

To update to the latest version:

```bash
uv tool upgrade kubectl-jsonlogs
```

### k9s Plugin Configuration

Add the following to `~/Library/Application Support/k9s/plugins.yaml` (macOS) or `~/.config/k9s/plugins.yaml` (Linux):

```yaml
plugins:
  json-logs:
    shortCut: Ctrl-J
    confirm: false
    description: "Pretty JSON Logs"
    scopes:
      - container
    command: bash
    background: false
    args:
      - -c
      - "kubectl-jsonlogs $POD $NAMESPACE $CONTEXT $NAME 2>&1 | less -R"
```

The plugin opens logs in `less` so you can scroll freely — press `q` to return to k9s.

**Key points:**

- The `container` scope means the shortcut is available from the k9s container view
- `$NAME` is the container name; logs are fetched from that container only
- Output is piped through `less -R` so ANSI colors are preserved and the view stays open until you press `q`
- Colors are always enabled in k9s mode (direct mode), regardless of TTY detection

To find your k9s config path:

```bash
k9s info
```

## Usage

### Pipe mode

```bash
kubectl logs <pod-name> | kubectl-jsonlogs
kubectl logs <pod-name> -c <container> | kubectl-jsonlogs
```

### Direct mode (used by the k9s plugin)

```bash
# All containers
kubectl jsonlogs <pod-name> <namespace> <context>

# Specific container
kubectl jsonlogs <pod-name> <namespace> <context> <container-name>
```

### k9s

1. Navigate to a pod's container view in k9s
2. Press `Ctrl-J`
3. Logs open in `less` with full color — scroll with arrow keys or `f`/`b`, quit with `q`

## Color Scheme

| Element | Color |
|---|---|
| JSON keys | Blue |
| String values | Green |
| Numbers | Magenta |
| Booleans | Yellow |
| Brackets / braces | Cyan |
| Timestamps | Gray |
| ERROR level | Red |
| WARN level | Yellow |
| INFO level | Cyan |
| DEBUG level | Gray |
| UUID / Enum tokens | Magenta |
| Python dict keys | Blue |

## Troubleshooting

**Plugin not found by kubectl:**

```bash
kubectl plugin list | grep jsonlogs
```

If missing, ensure `~/.local/bin` is in your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**Black screen and back in k9s:**

This happens when the plugin command exits immediately and k9s closes the overlay. Make sure your `plugins.yaml` uses the `bash -c "... | less -R"` form shown above — `less` keeps the output on screen until you press `q`.

**No colors in k9s:**

Ensure the plugin uses `less -R` (not plain `less`) so ANSI escape codes are rendered. The tool always emits colors in direct mode.

**No colors in pipe mode:**

Colors are disabled when stdout is not a TTY (e.g. redirected to a file). This is intentional for log aggregation compatibility.

**Performance:**

The script processes logs line-by-line for real-time streaming. For very high-volume logs consider using `jq` for pure JSON logs.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) for installation
