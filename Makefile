SHELL := /bin/bash

PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

.PHONY: help install config uninstall status plan logs activity test

help:
	@printf '%s\n' \
		'Monitask Center commands (thin wrappers around the monitask-center CLI):' \
		'  make install    Dev: install the CLI from this checkout (uv tool, editable); then run: monitask-center install' \
		'  make config     Open the interactive settings menu' \
		'  make uninstall  Remove the launchd job' \
		'  make status     Show workday, schedule and Monitask status' \
		'  make plan       Show today'"'"'s check plan' \
		'  make logs       Follow the Monitask log' \
		'  make activity   Send the activity rate to Telegram now' \
		'  make test       Run the test suite'

install:
	@cd "$(PROJECT_DIR)" && uv tool install --editable . --force

config:
	@monitask-center --config

uninstall:
	@monitask-center uninstall

status:
	@monitask-center status

plan:
	@monitask-center plan

logs:
	@monitask-center logs

activity:
	@monitask-center activity

test:
	@cd "$(PROJECT_DIR)" && uv run --group dev pytest -q
