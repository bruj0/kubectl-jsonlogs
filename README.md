# kubectl-jsonlogs Plugin

kubectl plugin to parse and colorize JSON and timestamped logs, similar to `jq` but optimized for Kubernetes log streams.

- **kubectl-jsonlogs**: Works as both a kubectl plugin and k9s plugin (pods/logs scope)

![Screenshot](./Screenshot%202025-12-09%20at%2010.54.42.png)

## Features

- **JSON Log Parsing**: Pretty-prints JSON logs with colorized output (similar to `jq`)
- **Pulumi Output Formatting**: Automatically detects and pretty-prints Pulumi output in `msg` fields with colorized resource status, outputs, and summaries
- **Timestamped Logs**: Automatically detects and colorizes timestamps in non-JSON logs
- **Log Level Detection**: Colorizes log lines based on detected log levels (ERROR, WARN, INFO, DEBUG)
- **Minimal Dependencies**: Uses only Python standard library (no external modules required)
- **Virtual Environment Support**: Optional venv loader for future extensions

## Installation

### Option 1: Direct Installation (Recommended)

1. Make the script executable and copy it to a directory in your PATH:

   ```bash
   chmod +x kubectl-jsonlogs
   cp kubectl-jsonlogs $HOME/.local/bin
   ```

2. Verify installation:
   ```bash
   kubectl jsonlogs --help
   ```

### Option 2: Symlink to kubectl plugin directory

```bash
# Create kubectl plugin directory if it doesn't exist
mkdir -p ~/.kubectl/plugins

# Create symlink
ln -s /path/to/workspace/kubectl-jsonlogs ~/.kubectl/plugins/kubectl-jsonlogs
```

### k9s Plugin Configuration

#### Option 1: Use the provided plugins.yaml (Recommended)

A ready-to-use `plugins.yaml` file is included in the repository. To use it:

1. Copy the plugins.yaml to your k9s config directory:

   ```bash
   k9s info # check the plugin path
   mkdir -p ~/.config/k9s
   cp /path/to/workspace/plugins.yaml ~/.config/k9s/plugins.yaml
   ```

2. Or merge it into your existing `~/.config/k9s/config.yml` under the `plugins:` section.

#### Option 2: Manual Configuration

To manually configure `kubectl-jsonlogs` for k9s, add the following to your `~/.config/k9s/config.yml`:

```yaml
plugins:
  json-logs:
    shortCut: Ctrl-J
    confirm: false
    description: "Pretty JSON Logs (All Containers)"
    scopes:
      - container
    command: kubectl
    background: false
    args:
      - jsonlogs
      - $POD
      - $NAMESPACE
      - $CONTEXT
      - $NAME
```

**Note:**

- The `container` scope allows you to use the plugin from the container view in k9s
- The plugin shows logs from the **selected container** only
- k9s variables: `$POD` (pod name), `$NAMESPACE`, `$CONTEXT`, `$NAME` (container name)
- When container is not specified (e.g., direct command line usage), the plugin uses `--all-containers` flag to tail logs from all containers in the pod
- Container names are highlighted in bold cyan to distinguish logs from different containers

Make sure `kubectl-jsonlogs` is in your PATH for k9s to find it.

## Usage

### kubectl-jsonlogs (Direct Mode or k9s Plugin)

**As kubectl plugin (pipe mode):**

```bash
kubectl logs <pod-name> | kubectl jsonlogs
```

**As k9s plugin (container scope):**

1. Navigate to a container in k9s container view
2. Press `Ctrl-J` (or your configured shortcut)
3. The plugin will automatically fetch and colorize logs from the **selected container** with pretty JSON formatting
4. Container names are highlighted in bold cyan when viewing logs from multiple containers

**Direct usage (with arguments):**

```bash
# Show logs from all containers (when container is not specified)
kubectl jsonlogs <pod-name> <namespace> <context>

# Show logs from a specific container
kubectl jsonlogs <pod-name> <namespace> <context> <container-name>
```

**Why use kubectl-jsonlogs for k9s?**

- Works from container view (scope: `container`)
- Shows logs from the **selected container** only
- Container names are colorized and highlighted for easy identification
- Automatically formats and colorizes JSON logs
- Better integration with k9s workflow

### Examples

**JSON Logs:**

```bash
kubectl logs my-app | kubectl jsonlogs
```

Output will be colorized JSON with:

- Blue keys
- Green strings
- Magenta numbers
- Yellow booleans
- Cyan brackets/braces

**Timestamped Logs:**

```bash
kubectl logs nginx-pod | kubectl jsonlogs
```

Output will have:

- Gray timestamps
- Red ERROR level logs
- Yellow WARN level logs
- Cyan INFO level logs
- Gray DEBUG level logs

**Mixed Content:**
The plugin automatically detects JSON objects within log lines and colorizes them appropriately.

**Pulumi Output:**
When JSON logs contain Pulumi output in the `msg` field, it is automatically detected and formatted as a multi-line, colorized block:

```bash
kubectl logs my-pulumi-app | kubectl jsonlogs
```

Pulumi output formatting includes:
- **Resource status lines**: Colorized based on status (green for creating/created, yellow for updating)
- **Section headers**: Bold cyan for "Outputs:", "Resources:", "Duration:"
- **Status indicators**: Yellow for "@ updating" lines
- **Output values**: Formatted with colored keys and values
- **Summary lines**: Formatted resource counts and durations

Example output:
```json
{
  "level": "info",
  "msg":
    Resource Updated Updating (resource):
    
     +  pulumi:pulumi:Stack my-stack creating (0s)
     +  aws:iam:Policy my-policy created (0.91s)
    
    Outputs:
        roleArn: "arn:aws:iam::123456789012:role/my-role"
    
    Resources:
        + 5 created
    
    Duration: 5s
}
```

## Virtual Environment Support

If you need to add optional dependencies in the future:

1. Create a virtual environment:

   ```bash
   cd /path/to/workspace
   python3 -m venv venv
   ```

2. Install any optional dependencies:

   ```bash
   source venv/bin/activate
   pip install <optional-package>
   ```

3. The script will automatically detect and use the venv if it exists in:
   - `$HOME/.venv/` (default location)
   - `$HOME/.kubectl-jsonlogs-venv/` (plugin-specific location)
   - `./venv/` (relative to script)
   - `./.venv/`
   - `../venv/`

## Color Output

Colors are automatically disabled when output is not a TTY (e.g., when piping to a file). This ensures compatibility with log aggregation tools.

## Requirements

- Python 3.6+
- No external dependencies (uses only Python standard library)

## Troubleshooting

**Plugin not found:**

- Ensure the script is in your PATH
- Verify the script is executable: `ls -l kubectl-jsonlogs`
- For k9s: Make sure `kubectl-jsonlogs` is accessible via `kubectl jsonlogs` command
- For k9s: Ensure the plugin is configured with scope `container` and uses correct variables: `$POD`, `$NAMESPACE`, `$CONTEXT`, `$NAME`

**No colors:**

- Colors are disabled when output is not a TTY
- Ensure you're running in a terminal that supports ANSI colors

**Performance:**

- The script processes logs line-by-line for real-time streaming
- For very high-volume logs, consider using `jq` for pure JSON logs
