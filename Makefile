SHELL := /bin/bash

PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SETUP := $(PROJECT_DIR)/setup.sh
INSTALL := $(PROJECT_DIR)/install.sh
RUN := $(PROJECT_DIR)/run.sh
LOG_FILE := $(HOME)/Library/Application Support/MonitaskCenter/monitask.log

.PHONY: help setup install reload uninstall status plan logs dump-windows check

help:
	@printf '%s\n' \
		'Monitask Center commands:' \
		'  make setup          Interactive first-time setup' \
		'  make install       Install and load launchd jobs' \
		'  make reload        Reload launchd jobs after changes' \
		'  make uninstall     Stop and remove launchd jobs' \
		'  make status        Show automation and Accessibility status' \
		'  make plan          Show today/yesterday schedule actions' \
		'  make logs          Follow the Monitask log' \
		'  make dump-windows  Inspect Monitask windows and buttons' \
		'  make check         Validate shell scripts'

setup:
	@"$(SETUP)"

install:
	@"$(INSTALL)" install

reload:
	@"$(INSTALL)" reload

uninstall:
	@"$(INSTALL)" uninstall

status:
	@"$(RUN)" status
	@"$(INSTALL)" status

plan:
	@"$(RUN)" plan

logs:
	@tail -f "$(LOG_FILE)"

dump-windows:
	@"$(RUN)" dump-windows

check:
	@bash -n "$(SETUP)" "$(INSTALL)" "$(RUN)"
	@git -C "$(PROJECT_DIR)" diff --check
	@echo "Checks passed."
