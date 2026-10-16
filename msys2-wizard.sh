#!/usr/bin/env bash
# msys2-init.sh — wizard for a Zsh + tmux + MSYS2/MINGW development environment
# Never writes to MINGW_PACKAGE_PREFIX. Safe to re-run.

set -euo pipefail

# ============================================================================
# 0.  Helpers
# ============================================================================
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*"; }

confirm() {
    local prompt="${1:-Continue?}"
    local answer
    read -r -p "$(printf "${YELLOW}%s [y/N] " "$prompt")" answer
    [[ "$answer" =~ ^[Yy] ]]
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        error "Required command '$1' not found. Aborting."
        exit 1
    fi
}

# ============================================================================
# 1.  Environment detection
# ============================================================================
# MINGW_PACKAGE_PREFIX is set by MSYS2's mingw environments; never touch it.
if [[ -n "${MINGW_PACKAGE_PREFIX:-}" ]]; then
    MODE="mingw"
    info "Detected MINGW environment (prefix: ${MINGW_PACKAGE_PREFIX})"
else
    MODE="msys2"
    info "Detected MSYS2 environment (no MINGW_PACKAGE_PREFIX)"
fi

# Shell mode is chosen by the user in step 2.
SHELL_MODE="bash"           # bash | oh-my-zsh
RC_FILE="${HOME}/.bashrc"   # updated by choose_shell_mode

# ============================================================================
# 2.  Choose shell mode (bash is the default)
# ============================================================================
choose_shell_mode() {
    info "Step 2 — Choose shell mode …"
    echo
    echo "    1) bash (default)"
    echo "         • configs written to ~/.bashrc"
    echo "         • zsh is NOT installed"
    echo "         • tmux default-shell is NOT changed"
    echo
    echo "    2) oh-my-zsh"
    echo "         • installs zsh + oh-my-zsh"
    echo "         • copies /usr/share/oh-my-zsh template to ~/.zshrc"
    echo "         • configs written to ~/.zshrc (appended AFTER oh-my-zsh init)"
    echo "         • tmux default-shell set to /usr/bin/zsh"
    echo
    local choice
    read -r -p "$(printf "${YELLOW}Select [1/2] (default: 1): ${NC}")" choice
    case "${choice:-1}" in
        2)
            SHELL_MODE="oh-my-zsh"
            RC_FILE="${HOME}/.zshrc"
            ;;
        *)
            SHELL_MODE="bash"
            RC_FILE="${HOME}/.bashrc"
            ;;
    esac
    ok "Shell mode: ${SHELL_MODE}  (rc file: ${RC_FILE})"
}

# ============================================================================
# 3.  Install base packages via pacman (MSYS2 side)
# ============================================================================
# NOTE: jq and git are intentionally NOT installed here — see step 5, where
# the user chooses between pacman / pacman-mingw / skip for each.
# NOTE: perl-text-csv is deliberately NEVER installed.
# NOTE: zsh is installed here ONLY in oh-my-zsh mode.
step_install_base() {
    info "Step 3 — Installing base packages …"

    local pkgs=(tmux pactoys)
    [[ "$SHELL_MODE" == "oh-my-zsh" ]] && pkgs+=(zsh)

    info "The following packages will be installed via pacman: ${pkgs[*]}"
    if confirm "Install ${pkgs[*]} via pacman?"; then
        pacman -S --needed --noconfirm "${pkgs[@]}" 2>&1 | tee /tmp/msys2-init-pacman.log
        ok "Base packages installed."
    else
        warn "Skipped base package installation."
    fi
}

# ============================================================================
# 4.  Install pacman-mingw wrapper + completions
# ============================================================================
step_install_pacman_mingw() {
    info "Step 4 — Installing pacman-mingw wrapper …"

    local wrapper_dir="${HOME}/.local/bin"
    mkdir -p "$wrapper_dir"

    cat > "${wrapper_dir}/pacman-mingw" <<'PACMAN_MINGW_EOF'
#!/usr/bin/env bash
# pacman-mingw — transparent pacman wrapper that injects $MINGW_PACKAGE_PREFIX
# when running inside a MINGW environment.  In MSYS2 mode it behaves exactly
# like plain pacman.

set -euo pipefail

if [[ -z "${MINGW_PACKAGE_PREFIX:-}" ]]; then
    # MSYS2 mode: pass everything through to pacman unchanged.
    exec pacman "$@"
fi

# MINGW mode: prefix any argument that looks like a package name.
# Package names are arguments that do NOT start with '-' and are not existing
# files.  We keep the original arguments and prepend the prefix.
declare -a new_args=()
for arg in "$@"; do
    if [[ "$arg" == -* ]]; then
        new_args+=("$arg")
    elif [[ -e "$arg" ]]; then          # allow file paths (e.g. -U ./pkg.tar.zst)
        new_args+=("$arg")
    else
        # Heuristic: if the arg already starts with "mingw-w64-" leave it alone
        if [[ "$arg" == mingw-w64-* ]]; then
            new_args+=("$arg")
        else
            new_args+=("${MINGW_PACKAGE_PREFIX}-${arg}")
        fi
    fi
done

exec pacman "${new_args[@]}"
PACMAN_MINGW_EOF

    chmod +x "${wrapper_dir}/pacman-mingw"
    ok "pacman-mingw installed to ${wrapper_dir}"

    # Make pacman-mingw available to the rest of this script immediately.
    export PATH="${wrapper_dir}:$PATH"

    # ----- Zsh completions (only in oh-my-zsh mode) ------------------------
    if [[ "$SHELL_MODE" == "oh-my-zsh" ]]; then
        if [[ -f "$RC_FILE" ]] && grep -q "compdef pacman-mingw" "$RC_FILE"; then
            ok "Zsh completions for pacman-mingw already present."
        else
            if confirm "Add pacman-mingw completions to ${RC_FILE}?"; then
                cat >> "$RC_FILE" <<'ZSH_COMPDEF_EOF'

# --- pacman-mingw completions (share pacman completions) ---
if [[ -n "${MINGW_PACKAGE_PREFIX:-}" ]]; then
    compdef _pacman pacman-mingw=pacman
fi
ZSH_COMPDEF_EOF
                ok "Completions added to ${RC_FILE}"
            fi
        fi
    fi

    # Ensure ~/.local/bin is on PATH for future shells
    if [[ ":$PATH:" != *":${wrapper_dir}:"* ]]; then
        warn "${wrapper_dir} is not on PATH. Add it to your shell profile."
    fi
}

# ============================================================================
# 5.  Install jq and git — user chooses pacman / pacman-mingw / skip for each
# ============================================================================
step_install_jq_git() {
    info "Step 5 — Installing jq and git …"
    echo
    echo "  Both jq and git are needed by various tools (tmux-service uses jq;"
    echo "  development workflows use git).  Choose how each should be installed."
    echo

    _install_one() {
        local pkg="$1"
        local choice
        echo "  ── ${pkg} ──────────────────────────────────────────────"
        if [[ "$MODE" == "mingw" ]]; then
            echo "    1) pacman        — MSYS2-side ${pkg} (path: /usr/bin/${pkg})"
            echo "    2) pacman-mingw  — MINGW-side ${pkg} (pkg: ${MINGW_PACKAGE_PREFIX}-${pkg})"
            echo "    3) do not install — skip ${pkg} entirely"
        else
            echo "    1) pacman        — MSYS2-side ${pkg} (path: /usr/bin/${pkg})"
            echo "    2) pacman-mingw  — MINGW-side ${pkg} (in MSYS2 mode this is just pacman)"
            echo "    3) do not install — skip ${pkg} entirely"
        fi
        echo
        read -r -p "$(printf "${YELLOW}Select [1/2/3] (default: 1): ${NC}")" choice
        choice="${choice:-1}"

        case "$choice" in
            1)
                if confirm "Install ${pkg} via pacman?"; then
                    pacman -S --needed --noconfirm "$pkg" 2>&1 | tee -a /tmp/msys2-init-pacman.log
                    ok "${pkg} installed via pacman."
                else
                    warn "Skipped ${pkg} installation (via pacman)."
                fi
                ;;
            2)
                if [[ "$MODE" != "mingw" ]]; then
                    warn "Not in a MINGW environment — pacman-mingw will pass through to plain pacman."
                fi
                if confirm "Install ${pkg} via pacman-mingw?"; then
                    pacman-mingw -S --needed --noconfirm "$pkg" 2>&1 | tee -a /tmp/msys2-init-pacman.log
                    ok "${pkg} installed via pacman-mingw."
                else
                    warn "Skipped ${pkg} installation (via pacman-mingw)."
                fi
                ;;
            3)
                warn "Skipping ${pkg}."
                warn "Install it later with 'pacman -S ${pkg}' or 'pacman-mingw -S ${pkg}'."
                ;;
            *)
                warn "Invalid choice '${choice}' — skipping ${pkg}."
                ;;
        esac
        echo
    }

    _install_one "jq"
    _install_one "git"
}

# ============================================================================
# 6.  Install MINGW-side development packages via pacman-mingw
# ============================================================================
# NOTE: python-playwright does NOT exist in MINGW/MSYS2 repos — it is
# installed via pipx in step 7 instead.
step_install_dev_packages() {
    info "Step 6 — Installing MINGW development packages …"

    if [[ "$MODE" != "mingw" ]]; then
        warn "Not in a MINGW environment.  Skipping MINGW-side packages."
        warn "Open a 'MSYS2 MINGW64' shell and re-run this step if needed."
        return
    fi

    local packages=(
        aichat
        llama.cpp
        git
        uv
        rust
        nodejs
        clang-tools-extra
        neovim
        go
        roswell
        python-pipx
    )

    info "The following packages will be installed with pacman-mingw:"
    printf '  %s\n' "${packages[@]}"

    if confirm "Install these MINGW packages?"; then
        for pkg in "${packages[@]}"; do
            info "Installing ${MINGW_PACKAGE_PREFIX}-${pkg} …"
            pacman-mingw -S --needed --noconfirm "$pkg" 2>&1 | tee -a /tmp/msys2-init-pacman.log
        done
        ok "MINGW development packages installed."
    else
        warn "Skipped MINGW development packages."
    fi
}

# ============================================================================
# 7.  Install Playwright via pipx (MINGW-side)
# ============================================================================
# Workaround for https://github.com/msys2/MINGW-packages/issues/30922 :
# pipx implicitly prefers the `uv` backend whenever uv is on PATH, but uv
# does not recognize the MINGW platform string (e.g. mingw_x86_64_ucrt) and
# fails.  Setting PIPX_DEFAULT_BACKEND=pip forces pipx to use the pip/venv
# backend, which works correctly on MSYS2/MINGW.
step_install_playwright() {
    info "Step 7 — Installing Playwright via pipx …"

    if [[ "$MODE" != "mingw" ]]; then
        warn "Not in a MINGW environment.  Skipping Playwright installation."
        warn "Open a 'MSYS2 MINGW64' shell and re-run this step if needed."
        return
    fi

    if ! command -v pipx >/dev/null 2>&1; then
        warn "pipx not found.  Install it first with: pacman-mingw -S python-pipx"
        return
    fi

    # --- The critical MSYS2/MINGW workaround ---------------------------------
    export PIPX_DEFAULT_BACKEND=pip
    info "Set PIPX_DEFAULT_BACKEND=pip (avoids uv incompatibility on MINGW)."
    # ------------------------------------------------------------------------

    if confirm "Install Playwright via pipx?"; then
        if pipx install playwright 2>&1 | tee -a /tmp/msys2-init-pipx.log; then
            ok "Playwright installed via pipx."
        else
            warn "pipx install playwright failed — see /tmp/msys2-init-pipx.log"
            return
        fi

        if confirm "Download Playwright browser binaries (chromium)?"; then
            if playwright install chromium 2>&1 | tee -a /tmp/msys2-init-pipx.log; then
                ok "Chromium browser binaries installed."
            else
                warn "playwright install chromium failed — see /tmp/msys2-init-pipx.log"
            fi
        fi
    else
        warn "Skipped Playwright installation."
    fi
}

# ============================================================================
# 8.  Install oh-my-zsh (only in oh-my-zsh mode)
# ============================================================================
# Order matters: install the package, then copy the shipped template to
# ~/.zshrc (if ~/.zshrc does not yet source oh-my-zsh).  All later steps
# APPEND to ~/.zshrc, so our custom lines always come after
# `source $ZSH/oh-my-zsh.sh`.
step_install_oh_my_zsh() {
    info "Step 8 — Installing oh-my-zsh …"

    if [[ "$SHELL_MODE" != "oh-my-zsh" ]]; then
        info "bash mode selected — skipping oh-my-zsh installation."
        return
    fi

    local omz_dir="/usr/share/oh-my-zsh"
    local tmpl="${omz_dir}/templates/zshrc.zsh-template"

    # ---- 8a. Install the package -----------------------------------------
    if [[ -d "$omz_dir" ]]; then
        ok "oh-my-zsh already installed at ${omz_dir}."
    else
        if ! confirm "Clone and build fufurobot/oh-my-zsh-git-msys2?"; then
            warn "Skipped oh-my-zsh installation."
            return
        fi
        local build_dir
        build_dir="$(mktemp -d)"
        git clone https://github.com/fufurobot/oh-my-zsh-git-msys2.git "$build_dir"
        (cd "$build_dir" && makepkg -si --noconfirm)
        rm -rf "$build_dir"
        ok "oh-my-zsh installed."
    fi

    # ---- 8b. Copy the shipped template to ~/.zshrc ------------------------
    # This must happen BEFORE any later step appends our custom config.
    if [[ ! -f "$tmpl" ]]; then
        warn "Template ${tmpl} not found — skipping ~/.zshrc seed."
        warn "Create ~/.zshrc manually and re-run if needed."
        return
    fi

    if [[ ! -f "$RC_FILE" ]]; then
        cp "$tmpl" "$RC_FILE"
        ok "Seeded ${RC_FILE} from ${tmpl}."
    elif ! grep -q 'oh-my-zsh\.sh' "$RC_FILE"; then
        warn "${RC_FILE} exists but does not source oh-my-zsh.sh."
        if confirm "Prepend the oh-my-zsh template to ${RC_FILE}?"; then
            local tmp
            tmp="$(mktemp)"
            cat "$tmpl" "$RC_FILE" > "$tmp"
            mv "$tmp" "$RC_FILE"
            ok "Prepended oh-my-zsh template to ${RC_FILE}."
        else
            warn "Leaving ${RC_FILE} untouched — you must source oh-my-zsh.sh yourself."
        fi
    else
        ok "${RC_FILE} already sources oh-my-zsh.sh."
    fi
}

# ============================================================================
# 9.  tmux default shell → zsh (only in oh-my-zsh mode)
# ============================================================================
step_tmux_zsh() {
    info "Step 9 — Setting tmux default shell to zsh …"

    if [[ "$SHELL_MODE" != "oh-my-zsh" ]]; then
        info "bash mode selected — leaving tmux default-shell untouched."
        return
    fi

    local tmux_conf="${HOME}/.tmux.conf"
    if grep -q 'default-shell.*zsh' "$tmux_conf" 2>/dev/null; then
        ok "tmux default-shell already set to zsh."
    else
        if confirm "Add 'set -g default-shell /usr/bin/zsh' to ~/.tmux.conf?"; then
            echo 'set -g default-shell /usr/bin/zsh' >> "$tmux_conf"
            ok "tmux default-shell set to zsh."
        fi
    fi
}

# ============================================================================
# 10. Install tmux-service CLI (JSONL-backed)
# ============================================================================
step_install_tmux_service() {
    info "Step 10 — Installing tmux-service CLI …"

    local svc_dir="${HOME}/.local/bin"
    mkdir -p "$svc_dir"

    cat > "${svc_dir}/tmux-service" <<'TMUX_SERVICE_EOF'
#!/usr/bin/env bash
# tmux-service — launch/stop/manage services defined in ~/tmux-service.jsonl
#
# Each line of the JSONL file is one service object:
#   {"name":"web","cmd":"python -m http.server","layout":{"layout":"even-vertical"}}
#
# The optional "layout" field is a tmux layout spec, e.g.:
#   {"layout":"even-vertical"}    (default)
#   {"layout":"even-horizontal"}
#   {"layout":"main-vertical"}
#   {"layout":"tiled"}
#
# jq is required at runtime.

set -euo pipefail

JSONL_FILE="${TMUX_SERVICE_JSONL:-$HOME/tmux-service.jsonl}"
SESSION="${TMUX_SERVICE_SESSION:-services}"
WINDOW="${TMUX_SERVICE_WINDOW:-main}"
DEFAULT_LAYOUT="even-vertical"

# ---------- helpers -------------------------------------------------------
die() { echo "tmux-service: $*" >&2; exit 1; }
require_tmux() { command -v tmux >/dev/null 2>&1 || die "tmux not found"; }
require_jq()   { command -v jq   >/dev/null 2>&1 || die "jq not found (required to parse ${JSONL_FILE})"; }

iter_entries() {
    require_jq
    [[ -f "$JSONL_FILE" ]] || die "JSONL file not found: $JSONL_FILE"
    jq -r '[.name, .cmd, (.layout.layout // "'"${DEFAULT_LAYOUT}"'")] | @tsv' "$JSONL_FILE"
}

# ---------- commands ------------------------------------------------------
cmd_run() {
    require_tmux
    local -a cmds=("$@")
    local layout="$DEFAULT_LAYOUT"

    if [[ ${#cmds[@]} -eq 0 ]]; then
        require_jq
        [[ -f "$JSONL_FILE" ]] || die "JSONL file not found: $JSONL_FILE"

        local line
        while IFS= read -r line; do
            cmds+=("$line")
        done < <(jq -r '.cmd' "$JSONL_FILE")

        if [[ -s "$JSONL_FILE" ]]; then
            layout="$(jq -r --arg d "$DEFAULT_LAYOUT" '.layout.layout // $d' "$JSONL_FILE" | head -n1)"
        fi
    fi

    if [[ ${#cmds[@]} -eq 0 ]]; then
        die "No commands to run (JSONL file is empty and no args given)."
    fi

    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux new-session -d -s "$SESSION" -n "$WINDOW"
    else
        tmux new-window -t "$SESSION" -n "$WINDOW"
    fi

    local pane_index=0
    for cmd in "${cmds[@]}"; do
        if [[ $pane_index -eq 0 ]]; then
            tmux send-keys -t "$SESSION:$WINDOW.$pane_index" "$cmd" C-m
        else
            tmux split-window -v -t "$SESSION:$WINDOW"
            tmux send-keys -t "$SESSION:$WINDOW.$pane_index" "$cmd" C-m
        fi
        ((pane_index++)) || true
    done

    tmux select-layout -t "$SESSION:$WINDOW" "$layout"
    tmux select-pane  -t "$SESSION:$WINDOW.0"
    tmux attach -t "$SESSION"
}

cmd_add() {
    require_jq
    local name="${1:-}" cmd="${2:-}" layout="${3:-$DEFAULT_LAYOUT}"
    [[ -n "$name" ]] || die "add: missing <name>"
    [[ -n "$cmd"  ]] || die "add: missing <cmd>"

    jq -nc \
        --arg n "$name" \
        --arg c "$cmd" \
        --arg l "$layout" \
        '{name:$n, cmd:$c, layout:{layout:$l}}' >> "$JSONL_FILE"

    echo "Added ${name}."
}

cmd_remove() {
    require_jq
    local name="${1:-}"
    [[ -n "$name" ]] || die "remove: missing <name>"
    [[ -f "$JSONL_FILE" ]] || die "JSONL file not found: $JSONL_FILE"

    local tmp="${JSONL_FILE}.tmp"
    jq -c --arg n "$name" 'select(.name != $n)' "$JSONL_FILE" > "$tmp" \
        && mv "$tmp" "$JSONL_FILE"
    echo "Removed ${name}."
}

cmd_list() {
    require_jq
    [[ -f "$JSONL_FILE" ]] || die "JSONL file not found: $JSONL_FILE"
    if ! iter_entries | column -t -s $'\t'; then
        die "list: failed to render entries."
    fi
}

cmd_search() {
    require_jq
    local pattern="${1:-}"
    [[ -n "$pattern" ]] || die "search: missing <pattern>"
    [[ -f "$JSONL_FILE" ]] || die "JSONL file not found: $JSONL_FILE"

    jq -r --arg p "$pattern" \
        'select((.name + " " + .cmd) | test($p; "i"))
         | [.name, .cmd, (.layout.layout // "'"${DEFAULT_LAYOUT}"'")] | @tsv' \
        "$JSONL_FILE" | column -t -s $'\t'
}

# ---------- dispatch ------------------------------------------------------
case "${1:-run}" in
    run)    shift; cmd_run "$@" ;;
    add)    shift; cmd_add "$@" ;;
    remove) shift; cmd_remove "$@" ;;
    list)   shift; cmd_list "$@" ;;
    search) shift; cmd_search "$@" ;;
    *)      die "Unknown command: $1.  Use run/add/remove/list/search." ;;
esac
TMUX_SERVICE_EOF

    chmod +x "${svc_dir}/tmux-service"
    ok "tmux-service installed to ${svc_dir}"

    if [[ ! -f "${HOME}/tmux-service.jsonl" ]]; then
        : > "${HOME}/tmux-service.jsonl"
        ok "Created empty ~/tmux-service.jsonl"
    fi
}

# ============================================================================
# 11. Shell aliases: windows-home, msys2-temp
# ============================================================================
# Appended to ${RC_FILE}, i.e. ~/.bashrc in bash mode or ~/.zshrc in
# oh-my-zsh mode.  In oh-my-zsh mode this runs AFTER step 8 seeded ~/.zshrc
# from the oh-my-zsh template, so our lines come after the oh-my-zsh init.
step_shell_aliases() {
    info "Step 11 — Adding shell aliases (windows-home, msys2-temp) to ${RC_FILE} …"

    if [[ -f "$RC_FILE" ]] && grep -q "alias windows-home" "$RC_FILE"; then
        ok "Aliases already present in ${RC_FILE}."
        return
    fi

    if confirm "Add windows-home and msys2-temp aliases to ${RC_FILE}?"; then
        cat >> "$RC_FILE" <<'SHELL_ALIASES_EOF'

# --- MSYS2 convenience aliases ---
# Step into the Windows user home directory (HOMEPATH → msys2 path)
alias windows-home='cd "$(cygpath -u "$HOMEPATH")"'

# Force temporary directories to /tmp for the current shell
alias msys2-temp='export TMP=/tmp; export TEMP=/tmp'
SHELL_ALIASES_EOF
        ok "Aliases added to ${RC_FILE}"
    fi
}

# ============================================================================
# 12. Main wizard
# ============================================================================
main() {
    echo "============================================================"
    echo "  MSYS2 Init Wizard  (mode: ${MODE})"
    echo "============================================================"
    echo

    choose_shell_mode
    echo
    step_install_base
    echo
    step_install_pacman_mingw
    echo
    step_install_jq_git
    echo
    step_install_dev_packages
    echo
    step_install_playwright
    echo
    step_install_oh_my_zsh
    echo
    step_tmux_zsh
    echo
    step_install_tmux_service
    echo
    step_shell_aliases
    echo

    echo "============================================================"
    ok "Wizard finished.  Open a new shell to pick up changes."
    echo "    shell mode : ${SHELL_MODE}"
    echo "    rc file    : ${RC_FILE}"
    echo "============================================================"
}

main "$@"