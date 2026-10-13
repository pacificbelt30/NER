# NER
NER is not Enough Readable.


- [REQUIREMENTS](#requirements)
- [INSTALL dependent python packages](#install-dependent-python-packages)
- [INSTALL nltk dataset/model](#install-nltk-dataset/model)
- [USAGE](#usage)
  - [List of COMMAND](#list-of-command)

## REQUIREMENTS
- python
- pipenv
- NLTK tokenizer data is optional; the PDF translation pipeline does not need it.
- A translation backend: Google, TexTra, local Index-Translate-2B, or local Bergamot.
- TexTra account and key only when using TexTra.

## INSTALL dependent python packages
```
pyenv install 3.11
pipenv install
```

## Optional NLTK tokenizer data
Only needed when calling the library's text extraction helpers with `is_split=True`.
```python
python -m nltk.downloader punkt punkt_tab
```
## USAGE
```sh
python launcher.py <COMMAND> [<PATH-of-origin-PDF-file>]
```

### List of COMMAND
| COMMAND | DESCRIPTION |
| ---- | ---- |
| help | Display help<br>`$ python launcher.py translate <PATH-of-origin-PDF-file>`  |
| translate | Translate the specified file. config.yml can be edited to change the translation service and fonts to be used. The output file name is the basename of the <PATH-of-origin-PDF-file> followed by "_new".<br><br> example:<br> &nbsp;&nbsp;&nbsp;&nbsp;test.pdf -> test_new.pdf<br> `$ python launcher.py translate <PATH-of-origin-PDF-file>` |
| combine | Combine the original and translated(suffix "_new") files into a file with suffix "_all". By displaying them as a spread, you can compare the original and the translated text.<br> `$ python launcher.py combine <PATH-of-origin-PDF-file>`|

### Config file
We need to prepare a configuration file.
This will contain information about the translation service, fonts, etc.
The name of the configuration file should be "config.yml". An example configuration is available as "config_sample.yml".

```sh
cp config_sample.yml config.yml
```
  
```yaml
PRIORITY: 'GOOGLE'
GOOGLE:
  URL: 'https://translate.googleapis.com/translate_a/single'
TEXTRA:
  NAME: sample
  KEY: keysamplekeysamplekeysamplekeysamplekeysample
  SECRET: secretsamplesecretsamplesecretsamplesecretsample
  URL: 'https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/'
FONT: '' # Built-in Japanese font, or a path to a font file.
```

### Config
Set `PRIORITY` to `GOOGLE`, `TEXTRA`, `INDEX`, or `BERGAMOT`.
Only the selected backend's settings are required. The PDF workflow translates English to Japanese.
Online services are [みんなの自動翻訳＠TexTra](https://mt-auto-minhon-mlt.ucri.jgn-x.jp) and [Google](https://translate.google.co.jp/) Translate. DeepL is not supported.
If you use みんなの自動翻訳＠TexTra, you can choose from several endpoints.
For English-Japanese translation, the following two options are available:
- scienceNT: https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/science_en_ja/
- generalNT: https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/
  
### Local translation: Index-Translate-2B

NER connects to a local chat-completions server running
[IndexTeam/Index-Translate-2B](https://huggingface.co/IndexTeam/Index-Translate-2B).
The model server runs separately, so its dependencies do not need to be installed in NER's environment.
Use a runtime with Qwen3.5 support. For example, in a separate CUDA environment:

```sh
pip install -U vllm
vllm serve IndexTeam/Index-Translate-2B \
  --served-model-name IndexTeam/Index-Translate-2B \
  --host 127.0.0.1 --port 8001 --max-model-len 32768
```

Port 8001 avoids a conflict with NER's API server on port 8000.
The first server launch downloads model weights; prepare them beforehand for offline use.
See the [official serving guide](https://github.com/bilibili/Index-Translate/tree/main/inference/llm)
for hardware requirements and other compatible runtimes, including llama.cpp with GGUF weights.

For CPU inference, download `Index-Translate-2B.Q4_K_M.gguf` from the
[official GGUF repository](https://huggingface.co/IndexTeam/Index-Translate-2B-GGUF)
into `models/index/` and start a recent llama.cpp server:

```sh
llama-server -m models/index/Index-Translate-2B.Q4_K_M.gguf \
  --alias IndexTeam/Index-Translate-2B --host 127.0.0.1 --port 8001 \
  -ngl 0 -c 8192 --jinja --chat-template-kwargs '{"enable_thinking":false}'
```

This text-only PDF workflow does not need a vision projector. Both server examples
use the same model alias and work with the configuration below.

Use this minimal `config.yml`:

```yaml
PRIORITY: 'INDEX'
INDEX:
  BASE_URL: 'http://127.0.0.1:8001/v1'
  MODEL: 'IndexTeam/Index-Translate-2B'
  MAX_TOKENS: 4096
  TIMEOUT: 300
FONT: ''
```

`MODEL` must match the name served by the local server. `MAX_TOKENS` is the output
budget per text block; `TIMEOUT` is the request timeout in seconds. Translation
uses the official prompt and disables thinking. A truncated or empty result fails
the operation instead of saving an incomplete translation.
Only loopback URLs (`127.0.0.1`, `localhost`, or `::1`) are accepted. Local requests
ignore environment HTTP proxies and do not follow redirects.

### Local translation: Firefox / Bergamot

NER invokes the native `translator-cli` from
[Mozilla's current Firefox translation engine](https://github.com/mozilla/translations/tree/main/inference).
It uses a downloaded model directly and does not require Firefox to be running.
The old PyPI `bergamot` package is not required.

Build the native executable with Git, CMake (3.20 or newer), and a C++17 compiler.
This example uses a fixed source revision:

```sh
git clone https://github.com/mozilla/translations.git firefox-translations
git -C firefox-translations checkout 69455acaecbe8650cdba988dbcf7c10ca20e7c48
cmake -S firefox-translations/inference -B firefox-translations/inference/build \
  -DCMAKE_BUILD_TYPE=Release -DUSE_WASM_COMPATIBLE_SOURCE=ON \
  -DCOMPILE_CPU=ON -DCOMPILE_CUDA=OFF -DUSE_INTGEMM=ON -DSSPLIT_USE_INTERNAL_PCRE2=ON \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build firefox-translations/inference/build --target translator-cli -j 2
```

The initial build fetches upstream submodules. Then download the released English
to Japanese model from Mozilla's registry (run in NER's Python environment):

```sh
python scripts/download_bergamot_model.py
```

The script downloads and decompresses the model, vocabulary files, and lexical
shortlist into `models/bergamot/en-ja/`, verifies the model's published size and
SHA-256, and generates its `config.yml`. Downloads occur only during this explicit
setup step. Model files are excluded from Git.

Use this minimal NER `config.yml` (the two configuration files have separate roles):

```yaml
PRIORITY: 'BERGAMOT'
BERGAMOT:
  BINARY: './firefox-translations/inference/build/src/app/translator-cli'
  MODEL_CONFIG: './models/bergamot/en-ja/config.yml'
  NUM_WORKERS: 1
  TIMEOUT: 300
FONT: ''
```

`BINARY` can also be `translator-cli` if it is on your `PATH`. Paths are relative
to the working directory and support `~`. `NUM_WORKERS` controls the engine's CPU
workers. `TIMEOUT` is the timeout in seconds per block. In CLI mode, NER
starts the engine for each uncached text block. Use the persistent worker below to
keep the model loaded for the entire PDF.

#### Persistent Bergamot worker

From NER's repository directory, add the worker target to the same Mozilla build:

```sh
cmake -S firefox-translations/inference -B firefox-translations/inference/build \
  -DCMAKE_PROJECT_INCLUDE="$PWD/native/bergamot_worker.cmake"
cmake --build firefox-translations/inference/build --target ner-bergamot-worker -j 2
```

This reuses the previously compiled engine libraries. Add to `BERGAMOT`:

```yaml
  WORKER_BINARY: './firefox-translations/inference/build/ner-bergamot-worker'
```

`WORKER_BINARY` takes precedence over `BINARY`. The process starts at the first
nonempty block, preserves block boundaries and UTF-8 with a length-prefixed
protocol, and releases the model when the PDF job finishes. A timed-out or broken
worker is terminated and the operation fails. Existing `BINARY` configurations
continue to work.

### Translating PDFs with a local backend

After either setup, use the existing commands:

```sh
python launcher.py translate paper.pdf # paper_new.pdf
python launcher.py combine paper.pdf   # paper_all.pdf
```

The API's `/return_translated_file` endpoint uses the same selected backend.
`python api_server.py` creates upload storage automatically. Uploads are streamed
in 1 MiB chunks and limited to 50 MiB. Each request has a separate workspace,
removed after the response or on failure. PDF processing runs in a single worker
process so translation does not block the API event loop and PyMuPDF is not used
concurrently from Python threads. The unimplemented Japanese-to-English endpoint
returns HTTP 501.
Local translation does not need TexTra credentials or downloaded NLTK tokenizer
data. It does not contact an online translation service or fall back to one on
failure. PDF text extraction and layout behavior remain the same; scanned PDFs
still require OCR outside NER.

### Error handling and performance

Configuration must be valid YAML with a mapping at its root. Invalid backend
settings, missing models/fonts, damaged PDFs, encrypted PDFs, and PDFs with no
translatable text report explicit errors. The CLI returns a nonzero exit code on
failure; `info` and `combine` do not need translation configuration.

Every HTTP backend uses a reusable session, a finite timeout, and response
validation. `RETRIES` (default 1) applies only to temporary connection failures and
HTTP 429/500/502/503/504; an invalid or incomplete response is not retried.
`GOOGLE.REQUEST_INTERVAL` defaults to 0 seconds and `TEXTRA.REQUEST_INTERVAL` to
3 seconds. These intervals are measured between request starts; there is no
initial sleep. `TIMEOUT` defaults to 60 seconds for online services.

`CACHE_SIZE` (default 1024, 0 disables it) bounds the per-document cache of identical
source blocks. Successful results are reused; failures are not cached. Translated
PDFs and combined PDFs are saved to temporary files and replace the destination
only after a successful write. All translations must fit before source text is
removed. Font fitting uses a bounded binary search in half-point steps and fails
if no complete placement is possible.

Library callers should close resources with context managers:

```python
from ner import TextraConnection

with TextraConnection('paper.pdf') as job:
    job.get_pdf_info()
    job.post_page()
    output_path = job.insert()
```

Online translation failures now stop the operation too; original English text is
never substituted for a failed translation. API errors use HTTP 400 for invalid
PDFs, 413 for oversized uploads, 415 for non-PDF filenames, 502 for translation
failures, and 500 for server setup or storage failures.

### Web UI and job API

Run `python api_server.py`, then open `http://127.0.0.1:8000/`.
The Japanese UI is served with the API; no frontend build or external CDN is
needed. It supports file selection and drag-and-drop, original-file preview,
backend selection, upload and translation progress, cancellation, retry, and
downloads. Completed PDFs can be compared with synchronized page navigation and
zoom. The comparison preview uses bounded server-rendered PNG pages, so it does
not depend on a browser's embedded PDF viewer. Downloads retain selectable PDF text.

The UI remembers the most recent receipt in browser storage. Reloading restores
the job; temporary connection failures reconnect automatically. Failed jobs can
reuse the uploaded file and select another engine. Setup labels indicate validated
configuration, not a live health check of the translation engine. Local engines
keep translation text on the server; online engines send it to their provider.

New clients should use the job API:

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/api/capabilities` | Languages, upload limit, configured engines, retention period |
| POST | `/api/jobs` | Multipart `file`, optional `backend`, and `output=translated\|comparison`; returns HTTP 202 |
| GET | `/api/jobs/{id}` | State, queue position, block/page and current-chunk progress, error, and download links |
| GET | `/api/jobs/by-key/{key}` | Recover a receipt after an uncertain upload response |
| POST | `/api/jobs/{id}/cancel` | Stop a queued job immediately; running jobs stop between chunks |
| POST | `/api/jobs/{id}/retry` | Reuse a failed/cancelled upload; optional multipart `backend` and `output`; returns a new job |
| GET | `/api/jobs/{id}/files/{kind}` | Download `original`, `translated`, or `comparison`; `?inline=true` opens the PDF |
| GET | `/api/jobs/{id}/preview/{kind}?page=1` | PNG preview of an `original` or `translated` page |

Example:

```sh
curl -F file=@paper.pdf -F backend=BERGAMOT -F output=comparison \
  -H 'Idempotency-Key: unique-upload-receipt' http://127.0.0.1:8000/api/jobs
```

Use a fresh optional `Idempotency-Key` for each new upload. Re-sending a receipt
returns the accepted job rather than creating duplicate work; changing its
filename/backend/output returns HTTP 409. HTTP 202 responses include `Location`;
poll that URL about once per second, respecting `Retry-After`. States are `queued`,
`running`, `cancelling`, `succeeded`, `failed`, and `cancelled`. Completion is reported
only after the PDF files are ready. Preview failure does not discard a completed PDF.

Successful and failed job workspaces are retained for one hour after completion,
allowing repeated downloads and retries. Expired workspaces are removed. Metadata
survives a server restart; interrupted jobs report `SERVER_INTERRUPTED` and can be
retried. Unaccepted partial uploads are removed on restart. The queue accepts at
most 16 active jobs and processes one PDF at a time. Run **one API worker process**
for this local deployment; the queue is not distributed across multiple API workers.

New API errors have the shape
`{"error":{"code":"EMPTY_FILE","message":"…","retryable":false}}`.
HTTP 429 means the queue is full; HTTP 503 means the selected backend needs setup.
Internal paths and credentials are not included in job errors or capabilities.
Progress also includes `block_id`, `completed_chunks`, and `total_chunks` for the
current block. A `PROTECTED_CONTENT_INVALID` error means a protected URL/code ID
was changed or dropped; the document is stopped before original text is removed.
The existing `/return_translated_file`, `/return_origin_file`, and
`/return_translated_file_jaen` endpoints remain compatible with earlier clients.
Their temporary files are still removed after the response; they do not create
retained jobs.

`NER_CONFIG_PATH` optionally selects a configuration file and `NER_STORAGE_PATH`
selects the upload directory. Defaults are `config.yml` and `pdfstore/`.
The OpenAPI reference is available at `/docs`.

### Verification

```sh
pipenv install --dev
pipenv run python -m pytest test -q
```

Optional browser smoke test, with Playwright installed and a configured server:

```sh
node scripts/smoke_web_ui.cjs /path/to/sample.pdf
```

Set `NER_UI_URL` to test another local port, and `NER_PLAYWRIGHT_MODULE` to select an
existing Playwright installation. The test checks an actual upload and download,
reload recovery, empty-file validation, JavaScript errors, and mobile layout.

### PDF structure verification

Run a reproducible reconstruction audit without a translation server:

```sh
pipenv run python scripts/audit_pdf_layout.py --output /tmp/ner-layout
```

The script generates a two-page PDF with columns, short/styled headings, URLs,
monospace code, a list, a table, a colored background, and rotated text. It produces
`report.json`, source/current/prototype PDFs, and a side-by-side `comparison.pdf`
plus PNG previews. Assertions check protected character coordinates, code
indentation, link targets and visible hit areas, column bounds, color, rotation,
and a minimum readable font size in the prototype.

The current renderer is tested with **identity translations** to isolate layout
damage from model errors. The experimental renderer uses **fixture-supplied
regions, roles, and Japanese translations**. It proves placement capabilities;
it does not implement or validate automatic document understanding. This fixture
prototype is separate from the application. It keeps list/table text in English;
table reconstruction and translation are not demonstrated.

Observed limits **before the block-translation update below** (the script now
measures the checked-out implementation, so reruns can show improvements):

- Short headings and table cells can be excluded by the `>40` alphabetic-character
  filter. Paragraph extraction loses line positions, span styles, and rotation;
  content-stream order can put the right column before the left one.
- Even identity output loses both fixture hyperlinks, changes code coordinates
  and indentation, joins list items, and renders rotated text horizontally.
  Removing trailing line hyphens corrupts a wrapped URL's `local-models` path.
- A 1,000-character Japanese translation in a 100 × 20 point box is accepted at
  1pt. Successful fitting does not imply readable output.
- The prototype preserves code/URL coordinates and rebuilds a flowing hyperlink
  with a translated label. HTML placement controls alignment, line height, size,
  color, and rotation. Japanese link fragments need normalization: the tested
  PyMuPDF version emits some empty/adjacent annotations.
- Fixed inline code leaves small slots for surrounding prose. In the fixture,
  `呼び出し` cannot fit the original `Call ` slot above 8pt. Exact coordinates
  and unrestricted translated wording cannot always be retained simultaneously.

Optionally test the actual plain-text Bergamot pipeline:

```sh
pipenv run python scripts/audit_pdf_layout.py --output /tmp/ner-layout \
  --bergamot-worker /path/to/ner-bergamot-worker \
  --bergamot-model /path/to/model/config.yml
```

An optional `--bergamot-html-worker /path/to/experimental-worker` also tests a
**separately compiled** worker with `ResponseOptions.HTML = true`. The script
creates an experimental model configuration with `alignment: soft`; enabling HTML
without model alignments aborts native HTML restoration. With the tested en-ja
model, `<code>` keeps code and visible URLs intact, but a translated `<a>` label
can become empty despite preserving `href`. HTML token retention alone does not
guarantee correct semantic placement or a usable hyperlink. This mode is not
enabled in the application.

The next production design should retain page/region/line/span geometry and
styles, classify protected code/URLs separately from prose, and expose a choice
between fixed positions and paragraph reflow. Use a configurable font-size floor
and report overflow when text cannot fit; expand regions only within verified
column and neighboring-object boundaries. Low-confidence classification needs
an editable preview rather than silent destructive placement.

### Block translation adapted from Otranslator

The sibling Otranslator project was inspected read-only, especially
`src/translation/{pipeline,chunks,model-text,index-prompt,execution-policy}.ts`,
`src/core/{extractor,protection}.ts`, and `src/cache/key.ts`. NER implements the
same block/mask/validate/restore approach independently in Python; it does not
import or execute Otranslator. Its actual Bergamot grouping policy is 700
characters, despite older prose documenting larger groups. This is a grouping
and restoration policy, not evidence that all translations improve semantically.

NER's PDF extraction now keeps stable block IDs, heading/paragraph/list roles,
source lines and span fonts/colors/directions, nearby heading context, and
protected ranges. Short English headings/cells are eligible; numbered/bulleted
lines become separate units. Monospaced blocks with code syntax stay at their
original coordinates. Inline code, URLs, external link labels, email/IP/path and
command-option literals are masked before inference and restored exactly.
Linked labels are currently retained in the source language to preserve their
destination reliably. PDF classification remains heuristic, unlike DOM tags.

Within each block, splitting prefers sentence, clause, word, then Unicode
grapheme boundaries. Protected tokens remain indivisible. Source whitespace is
reattached locally, and chunks are reassembled into their original block. Defaults
are `BERGAMOT.CHUNK_CHARS: 700`, 2000 for other backends, and `MAX_CHUNKS: 128`.
Python limits count Unicode code points; they never split a grapheme cluster.

Model inputs use short `Xn` IDs. Missing, duplicate, invented, or extended IDs
fail validation; Japanese reordering is allowed. Index receives preservation
instructions, block kind, and heading context. Bergamot receives masked text
without adding the heading to its output. The per-document LRU cache separates
block role/context, exact protected originals, and chunking recipe. Incomplete
or invalid blocks are never cached. Unlike Otranslator's browser display fallback,
NER aborts an invalid document rather than silently mixing untranslated failures
into a successful PDF. There is no automatic switch to an online engine.

Protected inline runs flow with their translated paragraph using escaped HTML;
literal URLs and linked labels get clickable hit areas in their new positions.
The native Bergamot HTML/alignment mode is not needed. Japanese link fragments
are normalized after insertion. All blocks share a 1pt floor and may grow by at
most 36pt along each writing axis after checking column limits, other text,
images, vector strokes and previously planned translations. If they still cannot fit, the
PDF is not written. Standalone code retains exact positions; inline code can
move to follow Japanese word order. Paragraphs, headings and table cells use the
same HTML placement path; mixed span styles and arbitrary skew remain limited.

API/UI progress includes current-chunk counts, and cancellation is checked
between chunks. These changes are covered by literal/Unicode/chunk/cache/failure,
PDF placement, and API tests. Native Bergamot was also checked on the synthetic
two-page fixture: protected URL/identifier bytes and code-block coordinates were
preserved. Translation fluency and semantic fidelity require separate evaluation.

Reproduce the native check (includes a 3,057-character block, split into 5 chunks):

```sh
pipenv run python scripts/verify_block_translation.py --output /tmp/ner-blocks \
  --worker /path/to/ner-bergamot-worker --model /path/to/model/config.yml
```

### PDF library measurements and end-to-end acceptance (2026-10-13)

Install optional tooling and reproduce:

```sh
python -m pip install -r requirements-benchmark.txt
python scripts/benchmark_pdf_libraries.py --output pdf/library-benchmark --repeat 7
python -m pip install playwright==1.63.0
python -m playwright install chromium
python scripts/verify_end_to_end.py --output pdf/end-to-end \
  --worker /path/to/ner-bergamot-worker --model /path/to/model/config.yml
```

The measurement corpus has **5 self-authored documents / 6 pages**: mixed columns,
headings, links, code and a table; a deliberately reversed column stream; raster
and vector figures; an overfull translation; and a page with `/Rotate=90`.
Translations for layout experiments are fixed fixtures, independent of model
quality. Seven extraction runs open documents afresh after import warm-up. These
are one machine's medians for this tiny corpus, not broad performance estimates.
Tables and character geometry are separate diagnostics, excluded from text timing.

| Library (version) | Text extraction, 6 pages (median ms) | Text probes | Correct column order | Character boxes | Exact table |
| --- | ---: | ---: | --- | --- | --- |
| PyMuPDF 1.28.2 | 9.69 | 10/10 | no | yes | yes |
| pdfplumber 0.11.10 | 73.19 | 9/10 | no | yes | yes |
| pdfminer.six 20260107 | 47.06 | 10/10 | yes | yes | not exposed / not tested |
| pypdf 6.20.1 | 10.69 | 10/10 | no | no | not exposed / not tested |
| pypdfium2 5.14.0 | 5.59 | 10/10 | no | yes | not exposed / not tested |

The ten text probes are exact phrases/literals with whitespace ignored. Pdfplumber
missed the rotated phrase in its default text output. The column check requires
left introduction → left conclusion → right introduction → right conclusion;
row-wise interleaving does not pass. Only pdfminer.six with `detect_vertical=True`
passed this fixture. For the four engines exposing character boxes, the left edge
of the isolated Anchor text was within 0.01pt of the known x=40pt. This checks one
horizontal anchor, not general coordinate accuracy. Pypdf has text visitors and
transforms but its tested layout output does not expose character boxes.

Table detection returned all four cells with PyMuPDF and pdfplumber. NER now uses
PyMuPDF's ruled-cell detector before grouping prose. These four single-word labels
stay in their original cells under the eligibility policy; phrase-cell translation
and complex cell preservation are measured separately below. The original baseline
placed 2/4 translated labels correctly; that earlier result remains in Git history.

Unchanged-page round trips with PyMuPDF, pypdf, and pypdfium2 preserved page boxes,
URI annotation counts, and rendered pixels on all six pages: **0% changed channels**
using the same independent PDFium renderer before/after. This validates copying;
it says nothing about replacing translated text. Whole-page pixel differences
on translated outputs are diagnostic only because legitimate glyph changes are
expected; they are not scored as layout fidelity.

| Writer experiment | Normal documents | Overfull document | Heading color / rotated text | Original prose removed | Skipped table labels in source cells |
| --- | --- | --- | --- | --- | --- |
| NER current (HTML for all blocks, grow before shrink) | 4/4 written | written at 3.39pt, 1pt lower bound | retained / retained | yes | 4/4 |
| PyMuPDF HTML for all blocks (experimental) | 4/4 written | rejected at readable floor | retained / retained | yes | 4/4 |
| ReportLab 5.0.1 + pypdf overlay | 4/4 written | written below 2pt | replacement loses style/direction | no: original text remains extractable | 4/4 |

All writers use the same extracted blocks and Japanese fixture translations.
The HTML experiment additionally applies source color/direction and heading
alignment; these gains are implementation choices, not automatic library behavior.
ReportLab's shrinking Paragraph/KeepInFrame plus pypdf page merge was exercised;
an opaque overlay is not text deletion, and retained original links do not prove
that their hit areas follow visible translated text. ReportLab is useful for new
PDF generation, but this tested overlay is not suitable as NER's replacement path.
Writer elapsed times include validation and are not ranked as engine throughput.

NER's mixed fixture retains all **3 code lines and their character coordinates**,
identifier bytes, URL characters (ignoring line wrapping), and unchanged URI destinations.
Displayed URLs can contain extraction line breaks; raw substring matching fails
on the wrapped fixture even though its characters and clickable target are intact. Both columns stay within their
horizontal bounds. The graphic fixture preserves the raster image and vector plot
pixels exactly (one of each). Inline code/URLs intentionally flow with Japanese
text; their original coordinates are not required. This is a small controlled
fixture evaluation, not a fidelity percentage for arbitrary PDFs.

CLI acceptance used the actual native Bergamot model: info without translation
configuration, PDF translation, four-page alternating comparison, unchanged input,
and nonzero exit for a missing input. Real HTTP acceptance checked idempotency,
downloads, previews, asynchronously reported invalid PDF errors, cancellation of a
running job, and retry. Real Chromium checked upload/translation, PNG previews,
reload recovery, mobile width, and absence of JavaScript errors. Structured results
are tracked in `benchmarks/end_to_end_results.json`; generated PDF/PNG evidence
stays in the ignored `pdf/` directory. The full unit/integration suite passes 208
tests, including preservation of the cancellation exception across chunk callbacks.
Index protocol/prompt tests use a loopback fixture server, not a loaded Index model.

**Recommended next changes, supported by these results:**

1. Keep the implemented PyMuPDF all-block HTML path. Map Japanese
   normal/bold/italic font files explicitly; do not assume a fallback font retains
   source emphasis. The requested 1pt floor is enforced; overflow identifies the
   page and block. A per-block adjustment UI is deferred at the user's request.
2. Extend the implemented cell detector to irregular borderless layouts and
   multi-line borderless cells. Expose inferred cell bounds and classification in
   an editable preview; existing text alone cannot establish every intended grid.
3. Build column regions and their reading order before assigning heading context.
   A global y/x sort still interleaves columns. Pdfminer.six is a useful secondary
   analysis candidate, but its one-fixture success needs broader testing.
4. Broaden the current 0/90/180/270-degree and nonzero crop-box tests to mixed
   code/prose blocks, internal destinations, long wrapped links, mathematical
   text, arbitrary skew and scanned pages. Current heuristics and this six-page
   corpus cannot establish universal guarantees.
   An editable per-region preview should expose uncertain classification and fit.

Primary API references: [PyMuPDF text/HTML placement](https://pymupdf.readthedocs.io/en/latest/recipes-text.html),
[pdfplumber characters and tables](https://github.com/jsvine/pdfplumber),
[pdfminer layout objects](https://pdfminersix.readthedocs.io/en/latest/tutorial/extract_pages.html),
[pypdf extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html),
[pypdfium2 objects and rendering](https://pypdfium2.readthedocs.io/en/stable/python_api.html),
[ReportLab paragraphs](https://docs.reportlab.com/reportlab/userguide/ch6_paragraphs/).

### Automatic translation eligibility

PDF extraction selects prose by word structure and protected literals rather than
character count. Standalone words (`Installation`, `Ready`), acronym-only labels,
numbers, addresses, identifiers, code, and predominantly non-Latin text remain
in the original PDF. Short phrases (`Getting started`, `Go now.`) and instructions
with protected objects (`Read https://example.org/`) are eligible. A skipped heading
still provides context for subsequent prose. URLs, including `www.` / common bare
domains, and email addresses inside prose are protected before translation.

These are conservative heuristics for the current English-to-Japanese workflow,
not a language detector or named-entity recognizer. Explicit calls to the translation
service can still translate individual words. A selectable-text PDF with no eligible
prose saves unchanged without model requests; an empty/scanned PDF still reports
that OCR is needed. `PaperExtract.selection_decisions` records eligibility reasons.

### Table translation and cell placement

Ruled tables use PyMuPDF `find_tables(strategy='lines_strict')` before prose
grouping. Character centers assign text to each cell once, including merged cells.
Only eligible cell prose reaches the translator; words, numbers, standalone URLs
and code stay in their original font/position. Translated cells use HTML placement
with original color, inferred horizontal alignment, fixed cell bounds and a 1pt
floor. Cells cannot grow into their
neighbors. Overflow fails before redaction or replacement of an existing output.
Source glyph runs are redacted transparently, retaining backgrounds and grid lines.
Detection normalizes `/Rotate` to the raw text coordinate system and restores it;
0/90/180/270-degree pages have regression coverage.

Borderless grids are inferred conservatively when at least three compact,
regularly spaced rows repeat the same column origins. Closely spaced prose columns
are excluded. Ambiguous same-row labels are kept as separate bounded units instead
of concatenating them across columns. Irregular spacing, borderless merged cells,
multi-line borderless cells, arbitrary skew and scanned tables need further work;
this heuristic does not claim reliable reconstruction of every PDF table.

Two self-authored one-page fixtures exercise 19 cells. The ruled fixture includes
a merged cell, a multi-line cell, shaded rows, a clickable URL, words and numbers.
The borderless fixture contains four regularly spaced rows. The baseline is
commit `74187a0`, after eligibility changes but before table separation. Strict
placement requires the **entire expected translation to equal the target cell's
text**, ignoring extraction whitespace; neighboring cell text counts as failure.
Skipped cells require identical characters and bounding boxes rounded to 0.001pt.

| Fixture | Before: exact translated cells | After: fixed translations | After: real Bergamot | Skipped cells unchanged |
| --- | ---: | ---: | ---: | ---: |
| Ruled, merged and multi-line cells | 1/5 | 5/5 | 5/5 | 6/6 |
| Regular borderless grid | 0/6 | 6/6 | 6/6 | 2/2 |

The ruled fixture retains all 11 grid/background pixel probes and the URL's visible
address/click target. Native placement compares against the model's own returned
text, not a gold translation; these scores measure containment and preservation,
not translation fluency or semantic accuracy. The earlier loose substring probe
scored 3/5 and 2/6 for the baseline; strict equality rejects the mixed cell content.
Raw per-cell results and acceptance checks are in `benchmarks/table_results.json`.

```sh
python scripts/evaluate_pdf_tables.py --output pdf/table-audit/current
python scripts/evaluate_pdf_tables.py --output pdf/table-audit/borderless --borderless
python scripts/evaluate_pdf_tables.py --output pdf/table-audit/native \
  --worker /path/to/ner-bergamot-worker --model /path/to/model/config.yml
# Add --code-root /path/to/read-only-baseline-checkout for the same before probes.
```

PDF/PNG evidence is generated under the ignored `pdf/table-audit/` directory. The
full 208-test suite and native CLI/API/Chromium acceptance passed after these changes.
Table API reference: [PyMuPDF table detection and redaction](https://pymupdf.readthedocs.io/en/latest/page.html).

### Placement policy: minimum size

At the user's request the minimum placement font size is **1pt**, including table
cells, protected spans and the compatibility TextWriter size search. Translated
text is never enlarged beyond the source size (source sizes below 1pt start at
1pt). Complete fitting is still mandatory: long unbreakable words and text that
cannot fit at 1pt fail before original text is redacted or an output is replaced.
The earlier 8pt measurements above describe the previous policy. A per-block
adjustment / review UI is deferred; overflow continues to fail the job.

All production PDF translations now use the same PyMuPDF HTML placement path,
including ordinary prose, tables and protected literals. Plain and structured
translation callers share it. Source size is the initial maximum; block-level
color, bold/italic flags, detected alignment and cardinal writing direction are
applied to the HTML. Isolated model newlines are soft wraps; explicit paragraph
breaks and protected literal line breaks are retained. Mixed emphasis within a
translated sentence and arbitrary skew still need richer source/style mapping.

### Placement order: source size, safe expansion, then shrink

1. Try the original rectangle at the source font size, with normal word wrapping.
2. Try conservative expansion in verified free space, still at source size.
3. If those frames cannot fit, choose the best fitting scale down to **1pt**.
   If nothing fits, fail before redaction / output replacement. The deferred
   per-block adjustment UI and partial-success behavior are not implemented.

`PageLayoutSpace` snapshots source glyphs, images, vector shapes and table regions
once per page and reserves each chosen translation frame for subsequent blocks.
Frames may expand by at most 36pt in the text direction and 36pt in the line-wrap
direction, mapped to 0/90/180/270-degree writing. Widening and deepening are checked
together so diagonal obstacles cannot be bypassed. Detected column gutters,
visible crop bounds and containing colored panels constrain expansion. Centered
headings widen symmetrically; table cells and ambiguous fixed labels stay bounded.
Uncertain existing overlap keeps the original frame. Small vertical HTML padding
keeps CJK glyph bounds within the obstacle-checked frame.

The controlled six-page corpus now places **19/20 normal-document blocks at source
size** (the remaining rich block shrinks). The 1,000-character stress translation
fits at **3.39pt**, versus the earlier 1.5pt TextWriter result. This comes from
using available space before shrinking, not a claim about arbitrary PDFs or model
quality. Both columns remain within the fixture's original horizontal bounds;
image/vector pixel probes, code coordinates, background, heading color/alignment
and rotated text direction pass. The raw report includes per-block source/placed
rectangles and font sizes in `benchmarks/pdf_library_results.json`.

`benchmarks/layout_flow_results.json` records the 208-test run, real Bergamot
checks (including the 3,057-character / five-chunk block), native table containment
(5/5 ruled, 6/6 regular borderless), and real CLI/API/Chromium acceptance. Generated
PDF/PNG evidence is under `pdf/flow-layout/`. Library selection remains PyMuPDF.
