#!/usr/bin/env bash
# Run the dazzle-preservelib suite under real Linux, from a Windows dev box, via WSL.
# (Copied from dazzlecmd-lib's tests/one-offs/run_suite_under_wsl.sh; only the
# repo-specific names differ.)
#
# WHY THIS EXISTS
# Path handling and rename/overwrite semantics differ between Windows and
# POSIX (os.path is ntpath on Windows, posixpath elsewhere; os.rename onto an
# existing file replaces it silently on POSIX). A suite can pass locally and
# assert something false on Linux. This runs it natively.
#
# USAGE (from Git Bash on the Windows host)
#     bash tests/one-offs/run_suite_under_wsl.sh                        # whole suite
#     bash tests/one-offs/run_suite_under_wsl.sh tests/test_same_name_guard.py
#     WSL_DISTRO=Ubuntu-22.04 bash tests/one-offs/run_suite_under_wsl.sh
#     PYTEST_ARGS="-x -v" bash tests/one-offs/run_suite_under_wsl.sh    # extra pytest args
#
# WSL_DISTRO is optional; unset means the machine's DEFAULT distro (filekit's
# runner hard-coded "Ubuntu", which does not exist on a box whose distro is
# "Ubuntu-22.04", so it could not start).
#
# The Dazzle stack (this repo, dazzle-lib, dazzle-filekit, unctools,
# dazzle-linklib) is imported from the local source trees via PYTHONPATH --
# the same code the Windows run tests -- so nothing (no egg-info, no build
# artifacts) is written into the Windows working trees. Only third-party deps
# go into a venv inside WSL; it is shared with dazzlecmd-lib's runner
# (~/.cache/dazzlecmd-lib-venv), which already holds them. Override a sibling
# path with DAZZLE_LIB_SRC, FILEKIT_SRC, UNCTOOLS_SRC or LINKLIB_SRC (Git Bash
# form, /c/...).
#
# PREREQUISITE (one time, inside the distro, only if venv creation fails):
#     sudo apt install -y python3-venv
set -u

REPO_WIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
to_wsl() { printf '%s' "$1" | sed -E 's#^/([a-zA-Z])/#/mnt/\L\1/#'; }

REPO_WSL="$(to_wsl "$REPO_WIN")"
DAZZLE_LIB_WSL="$(to_wsl "${DAZZLE_LIB_SRC:-/c/code/dazzle-lib}")"
FILEKIT_WSL="$(to_wsl "${FILEKIT_SRC:-/c/code/filetoolkit/github}")"
UNCTOOLS_WSL="$(to_wsl "${UNCTOOLS_SRC:-/c/code/UNCtools/local}")"
LINKLIB_WSL="$(to_wsl "${LINKLIB_SRC:-/c/code/dazzle-linklib}")"
TARGET="${1:-tests/}"

DISTRO_ARGS=()
if [ -n "${WSL_DISTRO:-}" ]; then
    DISTRO_ARGS=(-d "$WSL_DISTRO")
fi

echo "  distro : ${WSL_DISTRO:-<default>}"
echo "  repo   : $REPO_WSL"
echo "  stack  : $DAZZLE_LIB_WSL $FILEKIT_WSL $UNCTOOLS_WSL $LINKLIB_WSL"
echo "  target : $TARGET"
echo

# -e runs bash directly. `wsl.exe -- cmd` instead hands the command line to
# WSL's shell to be parsed again, which expands $VENV (unset there) to an
# empty string before the inner script runs.
wsl.exe "${DISTRO_ARGS[@]}" -e bash -lc "
set -u
# ~/.cache, not /tmp: WSL clears /tmp when the distro restarts, which forced a
# full venv rebuild (and a PyPI download) on nearly every run.
VENV=\${XDG_CACHE_HOME:-\$HOME/.cache}/dazzlecmd-lib-venv
if [ ! -x \"\$VENV/bin/python\" ]; then
    echo '  creating venv ...'
    python3 -m venv \"\$VENV\" || exit 1
    \"\$VENV/bin/pip\" install -q --upgrade pip
    # third-party deps only -- the Dazzle stack comes from PYTHONPATH
    \"\$VENV/bin/pip\" install -q pytest 'pydantic>=2.0' 'networkx>=3.0' || exit 1
fi
cd '$REPO_WSL' || exit 1
# The venv's bin/ first on PATH, as Linux CI's setup-python does: tests that
# look for a 'python' command find one (the distro ships only 'python3').
export PATH=\"\$VENV/bin:\$PATH\"
PYTHONPATH='$REPO_WSL:$DAZZLE_LIB_WSL:$FILEKIT_WSL:$UNCTOOLS_WSL:$LINKLIB_WSL' \\
    \"\$VENV/bin/python\" -m pytest '$TARGET' -q -p no:cacheprovider ${PYTEST_ARGS:-}
"
rc=$?
echo
if [ "$rc" -eq 0 ]; then
    echo "  LINUX RESULT: pass"
else
    echo "  LINUX RESULT: FAIL (exit $rc)"
fi
exit "$rc"
