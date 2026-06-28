# gtestcov Install, Upgrade, And Rollback

Normal users only need this page: give the one-shot prompt below to an AI agent.

This is the only install and upgrade guide for `gtestcov`. It covers human
review points and the execution rules an AI agent such as OpenCode, Codex, or a
local weak-AI assistant must follow.

## For The User

Give the AI these inputs:

1. Install mode: `git` or `zip`.
2. Tool directory: where `gtestcov` should live.
3. C++ project root: where `gtestcov init` should run.
4. For git mode: branch or tag, usually `main`.
5. For zip mode: release zip path or release zip URL.

Recommended defaults:

- Use `git` mode if you want easier upgrades, branches, and diffs.
- Use `zip` mode if you only want to use the tool and do not need to edit it.
- Use native Linux when available. On Windows, WSL is supported; keep large C++
  projects under Linux paths such as `~/work/<project>`.

One-shot prompt:

```text
Please install gtestcov by following gtestcov/docs/install.md.
Install mode: git
Tool directory: ~/tools
Project root: ~/work/<my_cpp_project>
Branch or tag: main

Requirements:
1. Do not modify system network, drivers, registry, WSL installation, Hyper-V, or services.
2. If the target directory is not empty, stop and report before changing it.
3. Reuse an existing gtestcov virtual environment when one exists.
4. After installation, run gtestcov version, gtestcov install doctor, gtestcov init, and gtestcov codrax-check.
5. Write <project-root>/.gtestcov/install_report.md.
```

## AI Safety Rules

The AI may create a tool directory, clone or unpack `gtestcov`, create a Python
virtual environment only when no reusable one exists, refresh the editable
install when needed, run `gtestcov` checks, and write an install report.

The AI must not change Windows network settings, repair or reinstall WSL, edit
registry keys, delete services, remove drivers, change Hyper-V or virtualization
features, run broad cleanup/reset commands, or silently overwrite an existing
tool directory. If a command appears to require administrator privileges or
system repair, stop and ask the user.

## Virtual Environment Rule

Do not recreate the Python virtual environment on every install or upgrade.
Treat it as workstation state, separate from tool-source versions.

Recommended layouts:

```text
<tool-parent-dir>/
  gtestcov/
  .venv-gtestcov/
```

or:

```text
~/.local/share/gtestcov/
  tool_slots/A
  tool_slots/B
  .venv/
  current_slot
```

Rules:

- First install creates the virtual environment if it is missing.
- Reinstall and upgrade reuse the existing virtual environment.
- Run full dependency installation only when dependencies changed, the venv is
  missing, or `gtestcov install doctor` reports a broken install.
- `gtestcov upgrade apply` and `gtestcov rollback apply` automatically attempt
  the light refresh `python -m pip install --no-deps -e <tool-root>` after
  switching slots.
- If `venv_refresh.status` is `needs_ai_action`, the AI should pass
  `--venv <existing-gtestcov-venv>` and continue; do not ask the user to type
  pip commands.
- Never delete an existing virtual environment without asking the user.

## First Install

Before changing files, inspect the target tool directory. If it exists and is
not empty, stop and ask the user whether to use a different directory or
continue.

Git mode:

```bash
cd <tool-parent-dir>
git clone https://github.com/olivia-learning/gtestcov.git
cd gtestcov
git checkout <ref>
test -d ../.venv-gtestcov || python3 -m venv ../.venv-gtestcov
source ../.venv-gtestcov/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
gtestcov version
gtestcov install doctor --project-root <cpp-project-root>
gtestcov init --project-root <cpp-project-root>
gtestcov codrax-check --project-root <cpp-project-root>
```

Zip mode:

```bash
cd <tool-parent-dir>
unzip <release-zip> -d gtestcov_unpacked
cd gtestcov_unpacked
# If the zip unpacks into a nested folder, enter the folder with pyproject.toml.
test -d ../.venv-gtestcov || python3 -m venv ../.venv-gtestcov
source ../.venv-gtestcov/bin/activate
python -m pip install -U pip
python -m pip install -e .
gtestcov version
gtestcov install doctor --project-root <cpp-project-root>
gtestcov init --project-root <cpp-project-root>
gtestcov codrax-check --project-root <cpp-project-root>
```

CODRAX is installed separately by the user. After `gtestcov init`, run
`gtestcov codrax-check --project-root <cpp-project-root>`. If CODRAX is missing
or its CLI shape changed, report that result and do not guess.

After installation, write:

```text
<cpp-project-root>/.gtestcov/install_report.md
```

The report must include install mode, tool directory, project root, Python
executable, `gtestcov version` summary, `install doctor` result, `codrax-check`
result, failed commands, and the next suggested command.

## Upgrade

Do not upgrade by reinstalling over the old tool. Always inspect first:

Before choosing an upgrade, run `gtestcov version` and then
`gtestcov upgrade inspect`; do not hand-judge the version from folder names,
zip names, or Git branches alone.

```bash
gtestcov upgrade inspect \
  --tool-root <current-gtestcov-root> \
  --project-root <cpp-project-root> \
  --target-ref <tag-or-branch> \
  --install-mode zip|git
```

Show `old_version_detection_report.md` to the user and stop. Only after user
approval:

```bash
gtestcov upgrade apply \
  --upgrade-id <upgrade_id> \
  --project-root <cpp-project-root> \
  --approve-overwrite-tool-modifications
```

If the returned `venv_refresh.status` is `needs_ai_action`, rerun with the
existing venv path:

```bash
gtestcov upgrade apply \
  --upgrade-id <upgrade_id> \
  --project-root <cpp-project-root> \
  --venv <existing-gtestcov-venv> \
  --approve-overwrite-tool-modifications
```

Upgrade replaces the tool with a fresh version in the inactive A/B slot. It does
not automatically carry old user edits into the new tool. To restore reviewed
custom edits after upgrade:

```bash
gtestcov upgrade restore-custom --upgrade-id <upgrade_id>
```

## A/B Slots And Rollback

Tool slots:

```text
~/.local/share/gtestcov/tool_slots/A
~/.local/share/gtestcov/tool_slots/B
~/.local/share/gtestcov/current_slot
```

Project snapshots:

```text
<project-root>/.gtestcov/upgrade_slots/<upgrade_id>/old/.gtestcov
<project-root>/.gtestcov/upgrade_slots/<upgrade_id>/new/.gtestcov
<project-root>/.gtestcov/upgrade_state.json
```

List rollback points:

```bash
gtestcov rollback list --project-root <cpp-project-root>
```

Rollback requires explicit approval:

```bash
gtestcov rollback apply \
  --upgrade-id <upgrade_id> \
  --project-root <cpp-project-root> \
  --approve
```

If rollback returns `venv_refresh.status: needs_ai_action`, rerun with
`--venv <existing-gtestcov-venv>`. Rollback backs up the current new-version
state before restoring the old `.gtestcov` state.

## OpenCode Rules

OpenCode should use this file as the single source of truth for install,
upgrade, restore-custom, and rollback. It must show the old-version detection
report before upgrade, preserve `.gtestcov/runs/**`, CODRAX evidence, manual
review files, source change requests, and direct CODRAX audit logs, and avoid
all OS-level repair or cleanup actions unless the user explicitly approves that
specific command after seeing the risk.
