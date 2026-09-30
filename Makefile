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
		'  make setup         Interactive first-time setup' \
		'  make install       Complete first-time setup and load launchd jobs' \
		'  make reload        Reload launchd jobs after changes' \
		'  make uninstall     Stop and remove launchd jobs' \
		'  make status        Show automation and Accessibility status' \
		'  make plan          Show today/yesterday schedule actions' \
		'  make logs          Follow the Monitask log' \
		'  make dump-windows  Inspect Monitask windows and buttons' \
		'  make check         Validate scripts and run installer regression checks'

setup:
	@/bin/bash "$(SETUP)"

install: setup

reload:
	@/bin/bash "$(INSTALL)" reload

uninstall:
	@/bin/bash "$(INSTALL)" uninstall

status:
	@/bin/bash "$(RUN)" status
	@/bin/bash "$(INSTALL)" status

plan:
	@/bin/bash "$(RUN)" plan

logs:
	@tail -f "$(LOG_FILE)"

dump-windows:
	@/bin/bash "$(RUN)" dump-windows

check:
	@for script in "$(SETUP)" "$(INSTALL)" "$(RUN)" "$(PROJECT_DIR)/check-install.sh"; do bash -n "$$script" || exit; done
	@bash "$(PROJECT_DIR)/check-install.sh"
	@git -C "$(PROJECT_DIR)" diff --check
	@echo "Checks passed."
