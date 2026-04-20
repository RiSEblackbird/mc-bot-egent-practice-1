SHELL := /bin/bash

.PHONY: help setup-python run-python run-python-dev run-node run-node-dev test-node build-bridge compose-up compose-up-watch compose-up-linux-host

help:
	@printf "Available targets:\n"
	@printf "  setup-python          Create .venv and install Python dependencies\n"
	@printf "  run-python            Run the Python agent from the repository root\n"
	@printf "  run-python-dev        Run the Python agent with watchfiles hot reload\n"
	@printf "  run-node              Run the Mineflayer bot in production mode\n"
	@printf "  run-node-dev          Run the Mineflayer bot with tsx watch\n"
	@printf "  test-node             Run Node unit tests\n"
	@printf "  build-bridge          Build the Paper AgentBridge plugin\n"
	@printf "  compose-up            Start the default Docker Compose stack\n"
	@printf "  compose-up-watch      Start the default Docker Compose stack with --watch\n"
	@printf "  compose-up-linux-host Start Compose with Linux host-gateway aliases\n"

setup-python:
	bash scripts/setup-python-env.sh

run-python:
	bash scripts/run-python-agent.sh

run-python-dev:
	bash scripts/run-python-agent-watch.sh

run-node:
	bash scripts/run-node-bot.sh start

run-node-dev:
	bash scripts/run-node-bot.sh dev

test-node:
	bash scripts/run-node-bot.sh test

build-bridge:
	bash scripts/build-bridge-plugin.sh

compose-up:
	docker compose up --build

compose-up-watch:
	docker compose up --build --watch

compose-up-linux-host:
	docker compose -f docker-compose.yml -f docker-compose.host-services.yml up --build
