# mdv

A lightweight, **read-only**, tabbed Markdown viewer. It works like Notepad but shows Markdown rendered.

## Usage

```bash
mdv file1.md file2.md      # open files (adds tabs if mdv is already running)
```

Or double-click a `.md` file after running `./install.sh`.

## Keys

| Key                         | Action                 |
|-----------------------------|------------------------|
| Ctrl+O                      | Open file(s)           |
| Ctrl+W                      | Close tab              |
| Ctrl+Tab / Ctrl+Shift+Tab   | Next / previous tab    |
| Ctrl+F, F3, Shift+F3        | Find, next, previous   |
| Ctrl+= / Ctrl+- / Ctrl+0    | Zoom in / out / reset  |
| Ctrl+U (or right-click)     | Show original source   |
| F5                          | Reload                 |
| Ctrl+Q                      | Quit                   |

## Features

- [x] Tabs, drag and drop, recent files
- [x] Asks to refresh when the open file changes on disk (background tabs are checked when you switch to them)
- [x] Relative images and links (`.md` links open in a new tab, web links open in your browser)
- [x] Follows the system theme

Jump back to [Usage](#usage).

## Install / uninstall

`./install.sh` links `mdv` into `~/.local/bin` and makes it the default app for `.md` files.
`./uninstall.sh` reverts that and restores Ghostwriter as the default.

Requires Python 3 and PyQt5 (`sudo apt install python3-pyqt5 python3-xlib`; python3-xlib lets mdv come to the front when you open a file).

## License

MIT. See [LICENSE](LICENSE).
