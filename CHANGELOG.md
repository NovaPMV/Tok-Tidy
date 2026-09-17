# Changelog

## 0.4.3
- Fixed AI model downloads failing on some Windows PCs with "WinError 1314" (symbolic links). Models are now always stored as normal files. A download that failed this way is finished on the next attempt without downloading again.

## 0.4.2
- Removed the "Open the guide" link from the installer's last page.
- Clearer first-run wizard text; the cache can go straight into a folder named like "TokTidy Cache".
- The "can't find its cache" screen no longer shows command-line hints.

## 0.4.1
- Installer: the final "measuring installed size" step no longer fills the details list with thousands of lines.

## 0.4.0
- New name: **TokTidy**.
- New Windows installer: step-by-step setup that downloads everything, GPU or CPU-only engine, optional desktop shortcut, launch at the end, and a proper uninstaller (listed in Windows "Installed apps").

## 0.3.0
- TikTok-style colour theme and new icon.
- "Trio" is now "Splitscreen View"; "Image" is now "Search by Image".
- New colour picker with an Apply button; starts on white.
- "Both models" search (merged rankings) and multi-phrasing search; OpenCLIP is the default.
- TokTidy.exe launcher for source installs; the Desktop shortcut repairs itself after the folder moves.

## 0.2.0
- Desktop app: previews grid, search, drag to Premiere, trio, settings, diagnostics.

## 0.1.0
- Indexer, search and command line.
