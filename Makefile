.DEFAULT_GOAL := help

PYTHON ?= $(shell command -v python3.11 2>/dev/null || command -v python3)
VENV     := .venv
BIN      := $(VENV)/bin
STAMP    := $(VENV)/.installed
APP_NAME := mkhybrid-gui.app
PREFIX   ?= /Applications

.PHONY: help venv deps build test install clean distclean

help:
	@echo "使用可能なターゲット:"
	@echo "  make build      - .venv を用意し、PyInstallerで .app をビルドする"
	@echo "  make test       - .venv を用意し、pytest を実行する"
	@echo "  make install    - build を実行し、.app を \$$(PREFIX)（既定: /Applications）へインストールする"
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

deps: $(STAMP)

# --- 主要ターゲット ---------------------------------------------------

test: deps
	$(BIN)/pytest

build: deps
	$(BIN)/pyinstaller --noconfirm mkhybrid_gui.spec
	@echo ""
	@echo "ビルド完了。以下のいずれかで起動してください:"
	@echo "  open dist/mkhybrid-gui.app"
	@echo "  dist/mkhybrid-gui/mkhybrid-gui"
	@echo "(build/ はPyInstallerの中間作業ディレクトリのため直接実行しないでください)"

install: build
	rm -rf "$(PREFIX)/$(APP_NAME)"
	cp -R "dist/$(APP_NAME)" "$(PREFIX)/$(APP_NAME)"
	@echo "インストール完了: $(PREFIX)/$(APP_NAME)"

clean:
	rm -rf build dist
	rm -rf src/*.egg-info *.egg-info
	rm -rf .pytest_cache
	find . -type d -name '__pycache__' -not -path './$(VENV)/*' -exec rm -rf {} +
	find . -type f -name '*.pyc' -not -path './$(VENV)/*' -delete

distclean: clean
	rm -rf $(VENV)
