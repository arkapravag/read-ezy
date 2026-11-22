# ReadEzy

ReadEzy recolours the text of a PDF. Each line fades from one colour into
another, and the next line starts with the colour the previous line ended on.
The output is a normal PDF: fonts, images, links and selectable, searchable
text are kept as they were.

It comes in two forms that share one engine (`readezy_core.py`, which needs
only [pypdf](https://pypdf.readthedocs.io/)):

* **Command line:** a Python script, `readezy.py`.
* **Browser page:** `docs/index.html`, a single page that runs the same engine
  locally in the browser with [Pyodide](https://pyodide.org/). Files are
  processed on your own computer.

## Command line

### Setup

You need Python 3.9 or newer and git.

1. Download the code:

   ```bash
   git clone https://github.com/arkapravag/read-ezy.git
   ```

2. Go into the folder:

   ```bash
   cd read-ezy
   ```

3. (Optional) Create a virtual environment:

   ```bash
   python3 -m venv .venv
   ```

   Then turn it on. On macOS / Linux:

   ```bash
   source .venv/bin/activate
   ```

   On Windows (PowerShell):

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

4. Install pypdf:

   ```bash
   pip install -r requirements.txt
   ```

5. List the themes to check the setup:

   ```bash
   python readezy.py --list-themes
   ```

On some systems the commands are `python3` and `pip3` instead of `python`
and `pip`.

### Examples

Default theme (`ocean`, PDF style):

```bash
python readezy.py input.pdf output.pdf
```

Another theme:

```bash
python readezy.py input.pdf output.pdf --theme forest
```

Light text on a dark background:

```bash
python readezy.py input.pdf output.pdf --theme midnight
```

One PDF per theme, written into a folder:

```bash
python readezy.py input.pdf out_folder --all-themes
```

Only some pages:

```bash
python readezy.py input.pdf output.pdf --pages 1-5,8
```

Your own colours (four text colours, then the background):

```bash
python readezy.py input.pdf output.pdf --custom "#000000" "#1A5FD0" "#000000" "#D0461A" "#FFFFFF"
```

Web style (line breaks from the visual layout, off-white paper):

```bash
python readezy.py input.pdf output.pdf --style web --theme moonlight
```

All options:

```bash
python readezy.py --help
```

Put file names with spaces in quotes:
`python readezy.py "My Paper.pdf" "My Paper (colour).pdf"`.

## Browser page

To run it from a clone, serve the `docs` folder:

```bash
python3 -m http.server 8000 --directory docs
```

Then open http://localhost:8000. The page downloads Pyodide from the jsDelivr
CDN, so it needs an internet connection. It uses the pypdf wheel in `docs/`,
or downloads pypdf from PyPI if the wheel is missing.

## Themes

Each theme lists four line colours and a background. Lines cycle through
c1→c2, c2→c1, c3→c4, c4→c3.

| theme | c1 ↔ c2 | c3 ↔ c4 | background (PDF / web style) |
|---|---|---|---|
| `ocean` (default) | `#000000` ↔ `#1A5FD0` | `#000000` ↔ `#D0461A` | `#FFFFFF` / `#FAF9F6` |
| `forest` | `#000000` ↔ `#127A5A` | `#000000` ↔ `#8A3FB0` | `#FFFFFF` / `#FAF9F6` |
| `ink` | `#000000` ↔ `#283593` | `#000000` ↔ `#7B1F3A` | `#FFFFFF` / `#FAF9F6` |
| `slate` | `#000000` ↔ `#5A6872` | `#000000` ↔ `#5A6872` | `#FFFFFF` / `#FAF9F6` |
| `midnight` | `#F0F0F0` ↔ `#6CB4FF` | `#F0F0F0` ↔ `#F2A65A` | `#121212` |
| `moonlight` | `#F0F0F0` ↔ `#8C99A3` | `#F0F0F0` ↔ `#8C99A3` | `#121212` |

A theme or custom colour set with a dark background is drawn in night mode:
the page gets the dark background and the text uses the light colours.

### Styles

* **PDF style** (default): line breaks follow the order the PDF draws its
  text, and the colour cycle restarts on every page.
* **Web style**: line breaks follow the visual layout, and the colour cycle
  continues across pages.

### Command-line options

| option | meaning |
|---|---|
| `--style pdf\|web` | colouring style (default `pdf`) |
| `--theme NAME` | a theme from the table above |
| `--custom C1 C2 C3 C4 BG` | your own colours |
| `--all-themes` | write one PDF per theme into the output folder |
| `--pages 1-5,8` | only process these pages |
| `--restart-each-page` / `--continuous` | restart the colour cycle on every page, or keep it going (defaults: restart for `pdf`, continuous for `web`) |
| `--plain-paper` | `web` style: white paper instead of `#FAF9F6` |
| `--list-themes` | print all themes and exit |
| `--version` | print the version |

## How it works

* **Colour per letter:** each letter gets one solid colour on a cosine curve
  between the first and last letter of its line.
* **Finding lines (PDF style):** the engine follows the PDF's drawing
  instructions and measures each letter's position on a 150 dpi grid, taking
  text runs, font sizes and transparency groups into account. A new line
  starts when a letter is more than 75 grid units left of the previous one.
* **Finding lines (web style):** a new line starts when the text jumps 3 px
  left or 50 px up.
* **Applying colour:** each letter's fill colour is set inside the PDF's own
  content streams, replacing any colour the text already had.

## Developing

`docs/index.html` is generated. Edit `web/index.template.html` or
`readezy_core.py`, then rebuild:

```bash
python3 tools/build_web.py
```

## Notes

* Scanned PDFs have no text layer. Add one first with
  [OCRmyPDF](https://ocrmypdf.readthedocs.io/), then convert the result:

  ```bash
  ocrmypdf input.pdf input_ocr.pdf
  ```

  ```bash
  python readezy.py input_ocr.pdf output.pdf
  ```

* Password-protected PDFs need to be unlocked first.
* Dark themes change the page background and text colours only. Light boxes
  stay light, so light text drawn on them can be hard to see.

## License

ReadEzy is released under the [MIT License](LICENSE) and is provided as is,
without warranty of any kind. Third-party components and their licenses are
listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
