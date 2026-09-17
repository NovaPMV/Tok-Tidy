"""Start a TokTidy module with the engine's libraries, without the venv launcher.

The venv's python.exe has the absolute path of the real Python baked in, so it
breaks when the TokTidy folder is moved. The app therefore runs the real
(relocatable) Python directly and adds the libraries folder here:

    python -m toktidy._boot toktidy.server [args...]
"""
import os
import runpy
import site
import sys

packages = os.environ.get("TOKTIDY_SITE_PACKAGES")
if packages and os.path.isdir(packages):
    site.addsitedir(packages)   # also runs the libraries' .pth files

module = sys.argv[1] if len(sys.argv) > 1 else "toktidy.server"
sys.argv = [sys.argv[0]] + sys.argv[2:]
runpy.run_module(module, run_name="__main__", alter_sys=True)
