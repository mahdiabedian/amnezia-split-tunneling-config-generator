PYTHON ?= python3
NAME := amnezia-split

.PHONY: run test build clean

# One simple run: downloads the current list and writes
# ip-list-<YYYYMMDD-HHMMSS>.json in the current directory.
run:
	$(PYTHON) amnezia_split.py

test:
	$(PYTHON) -m unittest discover -s tests -v

# Standalone binary for the current platform (dist/amnezia-split).
build:
	$(PYTHON) -m pip install --quiet pyinstaller certifi
	$(PYTHON) -m PyInstaller --onefile --name $(NAME) amnezia_split.py

clean:
	rm -rf build dist *.spec __pycache__ tests/__pycache__
