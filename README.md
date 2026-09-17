<p align="center">
  <img src="app/assets/icon.png" width="96" alt="TokTidy icon">
</p>

<h1 align="center">TokTidy</h1>

<p align="center">
  Find matching clips in huge short-video collections: smooth previews, AI search, and drag-and-drop into Premiere Pro.<br>
  Windows 10/11 · runs entirely on your own PC · free and open source (MIT)
</p>

---

TokTidy is a desktop app for video editors who make **split-screen edits**, where several short clips play side by side. Those edits look best when the clips match, but finding matching clips by scrolling through tens of thousands of videos is slow.

TokTidy lets you browse the whole collection as a grid of moving previews and search it:

- **by description**, like `red dress + beach`;
- **by picture**, dropping in an image to find clips that look like it;
- **by colour**;
- **by speech**, finding clips where someone says a word or phrase;
- **by similarity**, finding clips like the ones you picked.

When you find what you want, drag the clips straight into Premiere Pro.

Everything runs locally. After the one-time download of the AI models, no videos, frames or searches leave your computer, and your original files are never changed or moved.

> TokTidy is an independent project. It is not affiliated with or endorsed by TikTok, ByteDance or Adobe.

## Contents

1. [Features](#features)
2. [System requirements](#system-requirements)
3. [Installing](#installing)
4. [First launch](#first-launch)
5. [Using TokTidy](#using-toktidy)
6. [How it works](#how-it-works)
7. [Where TokTidy keeps things](#where-toktidy-keeps-things)
8. [Updating](#updating)
9. [Uninstalling](#uninstalling)
10. [Troubleshooting](#troubleshooting)
11. [For developers](#for-developers)
12. [Privacy](#privacy)
13. [License and credits](#license-and-credits)

## Features

- **Preview grid.** Every clip plays as a small, muted, looping preview. Only the clips on screen play, so scrolling stays smooth even with 100,000+ clips.
- **Search by description.** Describe what you're looking for in plain English. Combine ideas with `+`, and push unwanted matches down with the **Not…** box.
- **Search by Image.** Click, drop or paste (Ctrl+V) a picture to find clips that look like it.
- **Colour search.** Pick a colour, a preset swatch or a hex code, then click **Apply** to rank clips by how much of that colour they contain.
- **Audio search.** Find clips by what's said in them. Results show the spoken line and when it was said.
- **Find Similar.** Find clips like one clip, or like a whole selection.
- **Two AI models, or both at once.** OpenCLIP (the default) and SigLIP 2. **Both models** merges their results.
- **Match timestamps.** Search results show where in a clip the best match is (for example ▶ 0:04), and clicking it jumps the preview there.
- **Drag to Premiere Pro.** Drag one clip or a whole selection. Premiere links to your original, full-quality files.
- **Used markers.** Clips you drag out are marked **Used** automatically. You can hide used clips, or clear the markers per folder or for everything.
- **Splitscreen View.** A pull-up tray with three vertical slots to try clips side by side, play them together, find clips similar to all three, and drag all three into Premiere.
- **Sorting and filtering.** Sort by name, folder, dates, length, size, resolution, brightness, motion, pace (number of cuts), or shuffle. Filter by folder.
- **Resumable indexing.** Stop at any time and continue later. Only new or changed clips are processed, and renamed or moved clips keep their index.
- **Movable library.** Previews, frames and search data can each be moved to another drive from Settings.
- **Diagnostics.** A one-click check of the installation, plus a report you can copy when asking for help.

## System requirements

| | Minimum | Recommended |
|---|---|---|
| Windows | Windows 10 or 11, 64-bit | |
| Memory | 8 GB RAM | 16–32 GB RAM for very large libraries |
| Graphics | Any (uses the CPU-only engine) | NVIDIA graphics card with 6 GB+ memory and a recent driver |
| Disk: program | about 9 GB (CPU-only) | about 13 GB (NVIDIA GPU version) |
| Disk: library | about 0.6 GB per 1,000 clips (previews, frames and search data) | a fast drive with plenty of room |
| Internet | Needed during installation | |

An NVIDIA card makes indexing and search many times faster. Without one, everything still works on the CPU, just more slowly, which mainly matters for large collections.

## Installing

1. Go to the [**Releases**](../../releases) page and download **`TokTidy-Setup-<version>.exe`**.
2. Double-click it.
   - Windows may show **"Windows protected your PC"** because the installer isn't code-signed. Click **More info → Run anyway**.
3. Follow the setup window:
   - **Welcome** shows how much disk space TokTidy needs.
   - **License:** click **I Agree**.
   - **Components:** choose what to install. Setup pre-selects the right AI engine for your PC, and the space needed updates as you choose.
   - **Install location:** the default is your user folder, so no administrator rights are needed.
   - **Installing:** setup downloads and prepares each part, showing its progress. This takes about 15–45 minutes depending on your internet speed.
   - **Finish:** leave **Launch TokTidy now** ticked to start right away.

### What gets installed

Everything goes into one folder (by default `%LOCALAPPDATA%\Programs\TokTidy`). Nothing is installed system-wide, and a Python or ffmpeg you already have is neither used nor changed.

| Component | What it's for | Download | On disk | Optional? |
|---|---|---|---|---|
| TokTidy app | The program itself | – | < 5 MB | Required |
| App window (Electron) | The window you use | ~115 MB | ~300 MB | Required |
| Private Python + libraries | Runs TokTidy's engine | ~450 MB | ~1 GB | Required |
| AI engine: **NVIDIA GPU** version | Runs the AI models on your graphics card | ~3 GB | ~5 GB | Pick one |
| AI engine: **CPU only** version | Runs the AI models on the processor | ~250 MB | ~0.9 GB | Pick one |
| Video tools (ffmpeg) | Reads videos, makes previews | ~190 MB | ~300 MB | Recommended |
| AI models | OpenCLIP, SigLIP 2, Whisper | ~6.7 GB | ~6.7 GB | Recommended (otherwise downloaded on first use) |
| Desktop shortcut | | | | Optional |

**Totals:** about **13 GB** with the NVIDIA GPU version, or about **9 GB** CPU-only. Setup also needs about 4 GB of temporary space while it works, which it frees at the end.

Every download comes from the project's official source (GitHub, pytorch.org, PyPI, Hugging Face). Downloads are checked against published checksums where the project provides them. If a step fails, for example because the connection drops, run the installer again: finished steps are skipped.

## First launch

A short guide appears the first time TokTidy opens:

1. **Library location.** Choose where TokTidy keeps its previews, frames and search data. It creates a `TokTidyCache` folder there. Pick a drive with enough room (about 0.6 GB per 1,000 clips). If you already have a library, choose **I already have a library**.
2. **Add folders.** Add your video folders one at a time, add every folder inside a parent folder at once, or drag folders in from File Explorer.
3. **Start indexing.** **Start a test run** indexes 50 clips per folder, so you can try everything quickly. **Index everything** does the whole collection. You can keep using the app while it runs.

## Using TokTidy

### The screen

| Area | What's there |
|---|---|
| **Top bar** | Video/Audio switch, search box, **Not…** box, **Search by Image**, **Colour**, model choice, **Search**, **Splitscreen View**, Settings (⚙) |
| **Second bar** | Sort order and direction, **Hide used**, **Details**, tile **Size**, **Per page**, refresh, and the selection count |
| **Sidebar** | Your folders with clip counts and indexing progress. The **Index new clips** panel is at the bottom. |
| **Grid** | Moving previews. Each tile shows the name, length, resolution, fps, size and folder (turn this off with **Details**). |

### Searching

- **Describe it.** Type in the search box and press **Enter**.
  - Examples: `red dress`, `gym outfit`, `at the beach`, `showing off shoes`.
  - Combine ideas with `+`: `red dress + beach`.
  - Type words in **Not…** to push those clips down, for example `night, indoor`.
- **Search by Image.** Click the button and choose a picture, drop one onto it, or copy one and press **Ctrl+V**.
- **Colour.**
  - Click **Colour**, then pick a shade, click a swatch, or type a code like `#FF0050`. Click **Apply**.
  - The popup stays open so you can try another colour. Press **Esc** or click elsewhere to close it.
- **Audio.** Switch to **Audio** and type a word or phrase. Small spelling mistakes are tolerated. Each result shows the spoken line, and **▶ 0:04** jumps to that moment.
- **Similar.** Click **Similar** under a clip, or select several clips and choose **Find similar** from the right-click menu.
- **Model.** Choose **OpenCLIP**, **SigLIP 2** or **Both models** in the dropdown, and the results update right away. The two models are good at different things, so if a search disappoints, try another.
- **Back to all clips.** Click **× Back to all clips** in the bar above the results.

Results are ranked, with **#1** as the best match.

### Selecting and dragging to Premiere

- **Selecting:** click to select a clip. **Ctrl+click** adds or removes clips, **Shift+click** selects a range, and **Ctrl+A** selects the whole page.
- **Dragging:** drag any selected clip into Premiere's Project panel or timeline, and all selected clips go with it. Premiere uses your **original files**.
- **Used markers:** dragged clips are marked **Used**. If you drop them back into TokTidy by accident, the mark is removed again. You can also mark clips yourself from the right-click menu.
- **Right-click menu:** Open original · Show in Explorer · Find similar · Add to Splitscreen View · Mark as used / not used · Copy file path · Select all.

> Premiere may refuse some formats (for example `.webm` or `.mkv`). TokTidy marks those clips with a small badge.

### Splitscreen View

Click **Splitscreen View** in the top bar.

- **Adding clips:** drop clips onto the three slots, or right-click a clip and choose **Add to Splitscreen View**.
- **Play together** restarts all three at once.
- **Find similar to these** searches for clips like all three.
- **Drag all to Premiere** sends all three together.
- Each slot has its own Open, remove and drag buttons, and **Clear** empties all three.

### Keeping the library up to date

After adding new videos to your folders, click **Index new clips** in the sidebar. Only new or changed clips are processed.

- **Moved or renamed a folder?** Right-click it in the sidebar and choose **Folder moved or renamed…**.
- **Moved clips:** clips moved between folders are recognised and keep their index.
- **Deleted clips:** use **Settings → Library & cache → Forget deleted clips** to clean up clips you've removed.

### Keyboard shortcuts

| Key | Action |
|---|---|
| Ctrl+F | Jump to the search box |
| Enter | Search, or open the selected clip |
| Esc | Close popups and menus, or clear the selection |
| Ctrl+A | Select all clips on the page |
| Alt+← / Alt+→ | Previous / next page |
| Ctrl+V | Search with the picture on your clipboard |

### Settings

| Tab | What you can change |
|---|---|
| **General** | Show tile details; mark clips as used when dragged; how many previews play at once; free GPU memory after idle minutes; clear used markers |
| **Search & AI** | Default model (including **Both models**); search each phrase several ways; turn each model on or off, or clear its data; speech transcription and Whisper size; GPU precision, batch size, models in GPU memory |
| **Indexing** | Seconds between sampled frames; duplicate-frame threshold; preview size, quality and encoder; clips processed at once; low priority; NVIDIA decoding |
| **Library & cache** | Move or relink each part of the library; measure sizes; forget deleted clips; retry failed clips |
| **Diagnostics** | Check the installation; copy a diagnostics report; open the log folder |

## How it works

### Two parts

TokTidy is made of two programs that start together:

- **The app window** is built with Electron. It shows the grid and controls, plays previews straight from disk, and handles the native drag and drop into Premiere.
- **The engine** is written in Python. It reads the videos, runs the AI models, stores results in a database and answers searches.

The window talks to the engine over a private connection on your own computer (`127.0.0.1`, with a random password for each session). If the engine stops, the window restarts it.

### Indexing: what happens to each clip

Clips are indexed once, and progress is saved per clip and per step. You can stop at any time, and the next run picks up where it left off. If a step fails for one clip, the others carry on, and failed clips can be retried from Settings.

1. **Scan.** TokTidy lists each folder and compares it with what it already knows.
   - New clips are added, and edited clips are queued to be redone.
   - Missing clips are remembered rather than forgotten, so a clip that was renamed or moved keeps its index. A quick fingerprint (file size plus the first and last 64 KB) is used to recognise them.
2. **Decode.** Each video is read from disk only once, and that single pass produces:
   - **frames:** one still every 2 seconds, saved as small JPEGs. Near-identical frames are skipped, so a static shot doesn't create duplicates;
   - **a preview:** see [Why previews exist](#why-previews-exist);
   - **motion data:** tiny greyscale frames used to measure movement and count cuts (pace);
   - **audio** for transcription;
   - **clip details:** length, resolution, frame rate, brightness and dominant colours.
3. **Embed.** Each saved frame is run through the AI search models, which turn it into an *embedding*, a list of numbers describing what's in the picture. Each model stores its own embeddings, so switching models is instant.
4. **Transcribe.** Whisper, a speech-recognition model, writes down what's said, with timestamps, and adds it to a full-text search index. Silent and music-only parts are skipped.

A few details:
- **Adding models later:** if you turn on a model or transcription after indexing, only that step runs, reusing the saved frames. No video has to be read again.
- **Computer resources:** only one AI model sits in graphics memory at a time while indexing, and indexing runs at low priority so your PC (and Premiere) stays responsive.

### How AI search works

OpenCLIP and SigLIP 2 were trained on huge numbers of images paired with captions. From that training they learned to place pictures and text in the same "meaning space": a photo of a beach and the words "at the beach" end up close together as numbers. Searching is then a matter of comparing numbers:

- **Description search.**
  - Your text is converted into the same kind of numbers as the frames. It's phrased several ways ("a photo of…", "a video still of…") and averaged, which makes results steadier.
  - It's then compared with **every saved frame of every clip**, all held in memory, so searches are fast.
- **Scoring.** A clip's score is its **best-matching frame**, which is also how TokTidy knows the match is at 0:04.
- **Combining and excluding.** `+` averages several ideas. Words in **Not…** subtract from a clip's score.
- **Frame handling.** Frames are padded into a square before the model sees them, rather than cropped, so the full vertical frame is used and heads and shoes aren't cut off.
- **Search by Image** converts your picture instead of text and compares it the same way.
- **Find Similar** averages all of a clip's frames into one overall fingerprint and looks for clips with similar fingerprints.
- **Both models** searches with each model and merges the two rankings (reciprocal rank fusion). Clips that both models rank highly come first. The two models score on different scales, so ranks are merged rather than raw scores.

These models are very good at scenes, outfits, colours, objects and settings. They're weaker at counting ("three people") and at negation inside a phrase ("a dress that isn't red"), which is what the **Not…** box is for.

**The other searches don't use the AI models:**
- **Audio search** looks through the transcripts, with fuzzy matching for misspellings.
- **Colour search** ranks clips by how much of their dominant colours are close to the one you picked. Closeness is measured the way people perceive colour, not by raw colour codes.

The AI models are unloaded from graphics memory after a few idle minutes, so Premiere gets that memory back.

### Why previews exist

Playing original files in a grid is slow. Originals are often high resolution, and decoding dozens of them at once overwhelms a computer. That's why similar tools can only play a handful of clips at a time.

During indexing, TokTidy makes a small preview of every clip: full length, muted, 240p, at most 30 fps, and encoded on the graphics card when possible. These are tiny and cheap to play, so 20–48 of them loop smoothly at once. The grid only plays tiles that are on screen and releases the rest as you scroll.

Previews are only for looking. Dragging a clip into Premiere hands over the **original file**, so your edit always uses full-quality footage. Previews are the largest part of the library, roughly 0.5 MB per clip.

### Storage estimate

For 150,000 clips under 15 seconds:

| Part | Size |
|---|---|
| Previews | 60–75 GB |
| Frames | 8–10 GB |
| Search data (both models) | about 3 GB |
| Database | under 1 GB |
| **Total** | **roughly 75–90 GB** |

## Where TokTidy keeps things

| What | Where |
|---|---|
| The program, Python, AI engine, ffmpeg and AI models | The install folder (default `%LOCALAPPDATA%\Programs\TokTidy`) |
| Your library (previews, frames, search data, database) | The `TokTidyCache` folder you chose on first launch |
| Settings and logs | `%APPDATA%\TokTidy` |
| Temporary audio while transcribing | `%TEMP%\TokTidy` |
| Your videos | Where they already are. TokTidy only reads them. |

## Updating

Download the newest installer from [Releases](../../releases) and run it. It installs over the existing copy and skips downloads that are already up to date. Your library and settings are kept.

## Uninstalling

1. Close TokTidy.
2. Open **Settings → Apps → Installed apps** (Windows 11) or **Apps & features** (Windows 10), find **TokTidy**, and click **Uninstall**.
   - You can also run `Uninstall TokTidy.exe` in the install folder.
3. Choose what to remove:
   - **TokTidy program** (always removed): the app, its Python copy, the AI engine and the downloaded models.
   - **My library data** (optional): the previews, frames and search data TokTidy made. Your videos are never touched. Leave this unticked if you plan to reinstall, so you don't have to re-index.
   - **My settings** (optional): settings and log files in `%APPDATA%\TokTidy`.
4. Click **Uninstall**.

TokTidy doesn't install anything else on your system, so nothing is left behind beyond what you chose to keep.

## Troubleshooting

**"Windows protected your PC" when running the installer**
Click **More info → Run anyway**. The installer isn't code-signed, which is common for free open-source software.

**A setup step failed**
Check your internet connection and run the installer again. Finished steps are skipped. The details list in the setup window shows what went wrong.

**Setup says the GPU can't be used**
Update your NVIDIA driver from nvidia.com, restart, and run the installer again. Until then TokTidy works on the CPU.

**TokTidy doesn't start**
The error screen has **Copy error details** and **Open log folder** buttons. Running the installer again repairs a damaged installation.

**Previews are black or don't play**
Open **Settings → Diagnostics**, click **Run checks**, then **Copy diagnostics**, and include that in a bug report.

**Scrolling stutters**
In **Settings → General**, lower **Previews playing at once** (try 24), or make tiles bigger with the **Size** slider.

**"GPU out of memory"**
In **Settings → Search & AI**, lower **Frames per GPU batch** (try 16). If you use **Both models**, TokTidy switches between the models automatically when they don't fit in memory together.

**Premiere feels slow while indexing**
Keep **Run at low priority** on (**Settings → Indexing**), or stop indexing while you edit and resume later.

**Audio search finds song lyrics**
That's expected. Whisper writes down sung words too.

**The library "can't be found" after moving it**
TokTidy shows a screen with **Locate…** buttons. Point each one to the new location, and nothing needs to be rebuilt.

**Coming from TikTokSorter (the old name)?**
TokTidy picks up your old settings and library automatically. The installer also reuses AI models the old version already downloaded. After checking that everything works, you can delete the old program folder and `%USERPROFILE%\.cache\huggingface` to free about 7 GB.

## For developers

### Project layout

```
TokTidy/
├─ app/                  Electron app (plain HTML/CSS/JS, no build step)
│  ├─ main.js            starts the engine, window, native drag, dialogs
│  ├─ preload.js         safe bridge (window.toktidy)
│  ├─ renderer/          index.html, style.css, app.js
│  └─ assets/            icons
├─ backend/toktidy/      Python engine
│  ├─ server.py          local HTTP API used by the app
│  ├─ indexer.py         resumable per-clip pipeline
│  ├─ media.py           ffmpeg decode, previews, colours, motion
│  ├─ models.py          SigLIP 2 / OpenCLIP / Whisper, GPU memory manager
│  ├─ search.py          in-memory vector search, rank fusion, audio, colour
│  ├─ scanner.py         incremental scan, move/rename detection
│  ├─ db.py, cache.py, embstore.py, config.py
│  ├─ bootstrap.py       helpers used by the installer
│  └─ cli.py             command line (toktidy help)
├─ installer/            NSIS installer (TokTidy.nsi, bootstrap.ps1, build scripts)
├─ tools/                helpers for running from source
├─ setup.bat             developer setup (from source)
└─ requirements.txt
```

### Running from source

Requirements: Python 3.10–3.13 (64-bit), Node.js LTS, and ffmpeg on `PATH`.

1. Clone the repository.
2. Double-click `setup.bat`. It creates `.venv`, installs PyTorch (CUDA 12.8 build) and the requirements, installs Electron with npm, and builds `TokTidy.exe`, a small launcher for the source folder.
3. Start the app with `TokTidy.exe` or `Start TokTidy.bat`.

**Command line (advanced):**
- From source: `toktidy.bat help`.
- Installed copy: `engine\toktidy-cli.bat help` inside the install folder.

The app detects which layout it's running in:
- **Installed:** `resources\app` sits next to `engine\`.
- **From source:** `app\` sits next to `backend\` and `.venv\`.

### Building the installer

The installer is built with [NSIS 3](https://nsis.sourceforge.io), on Windows, Linux or macOS:

```bash
# Linux / macOS (sudo apt install nsis  or  brew install makensis)
REPO_URL=https://github.com/<you>/TokTidy ./installer/build.sh

# Windows (with NSIS installed)
installer\build.bat
```

- **Output:** `dist/TokTidy-Setup-<version>.exe`, plus a `.sha256` file. The version comes from `app/package.json`.
- **Heavy parts:** the installer is small (under 1 MB) because everything heavy is downloaded during installation. Pinned versions (Electron, uv, Python, PyTorch) are at the top of `installer/bootstrap.ps1`.
- **Automatic builds:** pushing a tag like `v0.4.1` runs `.github/workflows/build-installer.yml`, which builds the installer and attaches it to the GitHub release.

## Privacy

TokTidy has no accounts, analytics or telemetry.
- **What goes online:** only the installer's downloads, and model downloads on first use if you skipped them during setup.
- **What stays local:** indexing and search run entirely on your PC, and your videos, frames and searches never leave it.

## License and credits

TokTidy is released under the [MIT License](LICENSE).

It builds on excellent open-source work, including Electron, Python, PyTorch, FFmpeg, OpenCLIP, SigLIP 2 (Google), Whisper (OpenAI) with faster-whisper, Hugging Face Transformers, FastAPI and uv. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and sources.
