# mdv

A lightweight, tabbed Markdown viewer and editor. It works like Notepad but shows Markdown rendered;
press Ctrl+M to edit the source.

## Usage

```bash
mdv file1.md file2.md      # open files (adds tabs if mdv is already running)
mdv -n file3.md            # open a file in a separate window instead
mdv                        # open a new, empty window
```

Or double-click a `.md` file after running `./install.sh`.

## Keys

| Key                         | Action                 |
|-----------------------------|------------------------|
| Ctrl+Shift+N                | New window             |
| Ctrl+O                      | Open file(s)           |
| Ctrl+S                      | Save                   |
| Ctrl+W                      | Close tab              |
| Ctrl+Tab / Ctrl+Shift+Tab   | Next / previous tab    |
| Ctrl+F, F3, Shift+F3        | Find, next, previous   |
| Ctrl+= / Ctrl+- / Ctrl+0    | Zoom in / out / reset  |
| Ctrl+M or Ctrl+U            | Edit source / rendered |
| F5                          | Reload                 |
| Ctrl+Q                      | Quit                   |

## Features

- [x] Tabs, drag and drop, recent files, multiple windows (File → New Window, Ctrl+click or middle-click the taskbar entry, or `mdv -n`)
- [x] Edit the source and toggle back to see it rendered. Edits are saved automatically when you
      switch back to rendered, switch tabs, close a tab, quit, or switch to another app.
      Line endings, BOMs and non-breaking spaces are kept; files that aren't valid UTF-8 open read-only.
- [x] Asks to refresh when the open file changes on disk (background tabs are checked when you switch to them),
      or whether to keep your version if you have unsaved edits
- [x] Relative images and links (`.md` links open in a new tab, web links open in your browser)
- [x] Follows the system theme

Jump back to [Usage](#usage).

## Install / uninstall

`./install.sh` links `mdv` into `~/.local/bin` and makes it the default app for `.md` files.
`./uninstall.sh` reverts that and restores Ghostwriter as the default.

Requires Python 3 and PyQt5 (`sudo apt install python3-pyqt5 python3-xlib`; python3-xlib lets mdv come to the front when you open a file).

## License

MIT. See [LICENSE](LICENSE).
