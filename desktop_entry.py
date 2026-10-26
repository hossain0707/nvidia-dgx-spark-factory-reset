"""Standalone packaging entry point; no network actions occur on ordinary startup."""
from multiprocessing import freeze_support

from spark_recovery.desktop import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
