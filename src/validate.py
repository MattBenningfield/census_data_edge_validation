"""
Thin CLI launcher for the census validator.

Running this script puts src/ on sys.path, so the package imports resolve:

    py src/validate.py                      # validate the configured default file
    py src/validate.py path/to/file.csv     # validate a specific file

Equivalent to ``python -m census_validation`` when src/ is on PYTHONPATH.
"""

from census_validation.__main__ import main

if __name__ == "__main__":
    main()
