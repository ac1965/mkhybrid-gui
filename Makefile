.DEFAULT_GOAL := help

PYTHON ?= $(shell command -v python3.11 2>/dev/null || command -v python3)
VENV   := .venv
BIN    := $(VENV)/bin
STAMP  := $(VENV)/.installed

.PHONY: help venv install build test clean distclean

help:
	@echo "使用可能なターゲット:"
	@echo "  make build      - .venv を用意し、PyInstallerで .app をビルドする"
	@echo "  make test       - .venv を用意し、pytest を実行する"
	@echo "  make clean      - build/dist/キャッシュ等の生成物を削除する（.venvは残す）"
	@echo "  make distclean  - clean に加えて .venv も削除する"

# --- 仮想環境のセットアップ -----------------------------------------

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

venv: $(BIN)/python

$(STAMP): $(BIN)/python pyproject.toml
	$(BIN)/pip install -e ".[dev]"
	touch $(STAMP)

install: $(STAMP)

# --- 主要ターゲット ---------------------------------------------------

test: install
	$(BIN)/pytest

build: install
	$(BIN)/pyinstaller --noconfirm mkhybrid_gui.spec
	@echo ""
	@echo "ビルド完了。以下のいずれかで起動してください:"
	@echo "  open dist/mkhybrid-gui.app"
	@echo "  dist/mkhybrid-gui/mkhybrid-gui"
	@echo "(build/ はPyInstallerの中間作業ディレクトリのため直接実行しないでください)"

clean:
	rm -rf build dist
	rm -rf src/*.egg-info *.egg-info
	rm -rf .pytest_cache
	find . -type d -name '__pycache__' -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -type f -name '*.pyc' -not -path './$(VENV)/*' -delete

distclean: clean
	rm -rf $(VENV)
