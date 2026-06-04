"""Enable `python -m ascii2drawio ...`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
