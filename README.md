# ReadEzy

Turn any text PDF into a gradient-reading PDF. Each line fades from black into
a colour, and the next line starts with that same colour, so your eye can
find the next line.

There are two ways to use it:

* **Web app:** open the website, choose a PDF, pick a theme and download the
  result. It runs entirely in your browser, so your PDF is never uploaded.
* **Command line:** a Python script for batches and automation.

Both use the same engine (`readezy_core.py`), which needs only
[pypdf](https://pypdf.readthedocs.io/).

## Web app

The app lives in [`docs/index.html`](docs/index.html), a single page. It
loads [Pyodide](https://pyodide.org/) (Python compiled for the browser) from
the jsDelivr CDN, so it needs an internet connection.

### Put it online with GitHub Pages (one-time)

1. On GitHub, open the repository's **Settings** → **Pages**.
2. Under **Build and deployment**, set **Source** to *Deploy from a branch*.
3. Choose branch **`main`** and folder **`/docs`**, then press **Save**.

After a minute the app is live at:

```
https://arkapravag.github.io/read-ezy/
```

### Use it without hosting

Use the **Download this app** link at the bottom of the website, or copy
`docs/index.html` anywhere, then open the file in your browser. Keep
`docs/pypdf-6.21.4-py3-none-any.whl` next to it if you can; if it's missing,
the page downloads pypdf from PyPI instead.

To try it locally from a clone, serve the folder:

```bash
python3 -m http.server 8000 --directory docs
```

Then open http://localhost:8000.

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

3. (Optional, recommended) Create a virtual environment:

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

   Run the activate command again each time you open a new terminal.

4. Install the one package it needs (pypdf):

   ```bash
   pip install -r requirements.txt
   ```

5. Check it works by listing the themes:

   ```bash
   python readezy.py --list-themes
   ```

On some systems the commands are `python3` and `pip3` instead of `python`
and `pip`.

### Use

Convert a PDF with the default theme (`bright`, PDF style):

```bash
python readezy.py input.pdf output.pdf
```

Use a different theme:

```bash
python readezy.py input.pdf output.pdf --theme blues
```

Night theme (light text on black):

```bash
python readezy.py input.pdf output.pdf --theme inverted
```

Make one PDF for every theme, so you can compare them:

```bash
python readezy.py input.pdf out_folder --all-themes
```

Only convert some pages:

```bash
python readezy.py input.pdf output.pdf --pages 1-5,8
```

Your own colours (four text colours, then the background):

```bash
python readezy.py input.pdf output.pdf --custom "#000000" "#0000FF" "#000000" "#FF0500" "#FFFFFF"
```

Use the web style instead (softer colours on off-white paper):

```bash
python readezy.py input.pdf output.pdf --style web --theme night_blues
```

See every option:

```bash
python readezy.py --help
```

If a file name has spaces, put it in quotes:
`python readezy.py "My Paper.pdf" "My Paper (colour).pdf"`.

### Update to the latest version

From inside the `read-ezy` folder:

```bash
git pull
```

```bash
pip install -r requirements.txt
```

## Themes

### PDF style (default)

Vivid colours on white. Line breaks follow how the PDF draws its text, and
the colour pattern restarts on every page.

| theme | line colours | background |
|---|---|---|
| `bright` (default) | black ↔ `#0000FF` blue, black ↔ `#FF0500` red | white |
| `dark` | black ↔ `#00057F` navy, black ↔ `#970000` maroon | white |
| `blues` | black ↔ `#0000FF` blue, black ↔ `#840DD2` purple | white |
| `gray` | black ↔ `#666666`, black ↔ `#666666` | white |
| `inverted` | `#FBFBFB` ↔ `#56AAFF`, `#FBFBFB` ↔ `#9E8DFC` | black |

### Web style

Softer colours on off-white paper. Line breaks follow the visual layout, and
the colour pattern continues across pages.

| theme | line colours | background |
|---|---|---|
| `bright` | black ↔ `#0419ff`, black ↔ `#b10202` | `#FBFBFB` |
| `dark` | black ↔ `#000676`, black ↔ `#5e0200` | `#FBFBFB` |
| `blues` | black ↔ `#0000FF`, black ↔ `#840DD2` | `#FBFBFB` |
| `gray` | black ↔ `#666666`, black ↔ `#666666` | `#FBFBFB` |
| `night_gray` | `#FBFBFB` ↔ `#666666`, `#FBFBFB` ↔ `#666666` | black |
| `night_blues` | `#FBFBFB` ↔ `#56AAFF`, `#FBFBFB` ↔ `#9E8DFC` | black |

A custom theme with a dark background automatically uses night rendering.

### Command-line options

| option | meaning |
|---|---|
| `--style pdf\|web` | colouring style (default `pdf`) |
| `--theme NAME` | a theme from the tables above |
| `--custom C1 C2 C3 C4 BG` | your own theme |
| `--all-themes` | write one PDF per theme into the output folder |
| `--pages 1-5,8` | only process these pages |
| `--restart-each-page` / `--continuous` | restart the 4-line colour cycle on every page, or keep it going (defaults: restart for `pdf`, continuous for `web`) |
| `--plain-paper` | `web` style: white paper instead of `#FBFBFB` |
| `--list-themes` | print all themes and exit |
| `--version` | print the version |

## How it works

* **Line cycle:** c1→c2, c2→c1, c3→c4, c4→c3, then it repeats.
* **Colour per letter:** each letter gets one solid colour on a cosine curve
  between the first and last letter of its line.
* **Where letters are (PDF style):** the engine follows the PDF's drawing
  instructions and measures each letter on a 150 dpi canvas, taking text
  runs, font sizes and transparency groups into account. A new line starts
  when a letter is more than 75 canvas pixels to the left of the previous
  one.
* **Where letters are (web style):** a new line starts when the text jumps
  3 px left or 50 px up.
* **How colour is applied:** each letter's fill colour is set inside the PDF's
  own drawing instructions. It works on any background and replaces colours
  the text already had. Fonts, images, links and selectable/searchable text
  are untouched.

## Developing

`docs/index.html` is generated. Edit `web/index.template.html` or
`readezy_core.py`, then rebuild:

```bash
python3 tools/build_web.py
```

## Notes

* Scanned PDFs have no text layer, so there is nothing to colour. Make the
  text readable first with [OCRmyPDF](https://ocrmypdf.readthedocs.io/), then
  convert the result:

  ```bash
  ocrmypdf input.pdf input_ocr.pdf
  ```

  ```bash
  python readezy.py input_ocr.pdf output.pdf
  ```

* Password-protected PDFs need to be unlocked first.
* Dark themes only change the page background and the text colours. Light
  boxes stay light, so light text drawn on them can be hard to see.
