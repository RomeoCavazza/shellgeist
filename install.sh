#!/usr/bin/env bash
# ShellGeist One-Command Installer

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_PATH="$HOME/.local/bin/shellgeist"

echo "Installing ShellGeist..."

# 1. Ensure ~/.local/bin exists
mkdir -p "$HOME/.local/bin"

# 2. Create symlink to the wrapper
ln -sf "$REPO_DIR/shellgeist" "$BIN_PATH"

# 3. Add to PATH if not present
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "Adding $HOME/.local/bin to PATH in .bashrc"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    echo "Please restart your shell or run 'source ~/.bashrc'"
fi

chmod +x "$REPO_DIR/shellgeist"

echo "ShellGeist installed as '$(which shellgeist || echo $BIN_PATH)'"
echo "You can now run: shellgeist --help"
