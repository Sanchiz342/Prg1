# Zirka Tasks — Getting Started

> Demo document. Zirka Tasks is a fictional product invented for this project.

Zirka Tasks is a task tracker for small teams. You can use it from the web app, from the desktop app or from the
`zirka` command-line tool.

## System requirements

The desktop app runs on Windows 10 or newer, macOS 12 or newer and Ubuntu 22.04 or newer.
It needs at least 4 GB of RAM and 500 MB of free disk space.
The command-line tool needs Python 3.10 or newer.

## Installing the CLI

Install the CLI with pip: `pip install zirka-cli`.
After installation, check the version with `zirka --version`.
To upgrade an existing installation, run `pip install --upgrade zirka-cli`.

### Logging in

Run `zirka login` and follow the link that opens in your browser.
The CLI stores the session token in `~/.config/zirka/credentials.json` with file permissions 600.
To log out on a shared computer, run `zirka logout`, which deletes the stored token.

## Workspaces and projects

A workspace is the top-level container for your team. Every workspace has exactly one owner.
Inside a workspace you create projects, and inside projects you create tasks.
To create a project from the CLI, run `zirka project create "Website redesign"`.

### Inviting members

Workspace owners and admins can invite members from Settings › Members or with `zirka invite email@example.com`.
Invitations expire after 7 days. An expired invitation can be re-sent from the same page.

## Tasks

Each task has a title, an optional description, an assignee, a due date and a status.
The default statuses are To do, In progress, Review and Done; admins can add up to 10 custom statuses per project.
Tasks can be linked as "blocks" or "relates to". A task that is blocked by an open task cannot be moved to Done.

## Keyboard shortcuts

Press `C` to create a task, `/` to search and `G` then `P` to jump to the project list.
Press `?` anywhere in the web app to see the full list of shortcuts.
