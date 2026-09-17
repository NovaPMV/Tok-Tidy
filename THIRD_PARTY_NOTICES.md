# Third-party software

TokTidy's own code is MIT licensed (see `LICENSE`). The installer downloads the
following components from their official sources at install time. They are not
part of this repository, and each keeps its own license.

| Component | Used for | License | Source |
|---|---|---|---|
| Electron | App window | MIT (Chromium parts: BSD and others) | github.com/electron/electron |
| rcedit | Sets the TokTidy icon on the app executable | MIT | github.com/electron/rcedit |
| uv | Installs Python and libraries | MIT / Apache-2.0 | github.com/astral-sh/uv |
| Python (python-build-standalone) | Runs the engine | PSF License | github.com/astral-sh/python-build-standalone |
| PyTorch, torchvision | Runs the AI models | BSD-3-Clause (CUDA builds include NVIDIA libraries under NVIDIA's license) | pytorch.org |
| FFmpeg (BtbN builds, GPL variant) | Reads videos, makes previews | GPL-3.0 | github.com/BtbN/FFmpeg-Builds |
| Hugging Face Transformers, huggingface_hub | Loads SigLIP 2 | Apache-2.0 | github.com/huggingface |
| OpenCLIP (open_clip_torch) | Loads OpenCLIP | MIT | github.com/mlfoundations/open_clip |
| faster-whisper, CTranslate2 | Speech transcription | MIT | github.com/SYSTRAN/faster-whisper |
| FastAPI, Uvicorn | Local engine server | MIT / BSD-3-Clause | fastapi.tiangolo.com |
| NumPy, Pillow, RapidFuzz, psutil, tqdm | Various | BSD / MIT-CMU / MIT | PyPI |

## AI models (downloaded from Hugging Face)

| Model | Used for | License |
|---|---|---|
| google/siglip2-so400m-patch14-224 | Search by description or picture | Apache-2.0 |
| laion/CLIP-ViT-L-14-DataComp.XL-s13B-b90K (OpenCLIP) | Search by description or picture | MIT |
| Systran/faster-whisper-small (OpenAI Whisper) | Speech-to-text | MIT |

Check each project's page for the current terms before redistributing any of these.

TokTidy is an independent project and is not affiliated with, endorsed by, or
connected to TikTok, ByteDance, Adobe, or any other company mentioned here.
