#!/usr/bin/env bash
set -e
echo "Starting Infusion Solutions NL→SQL on port 7863..."
uvicorn ui_app:app --host 0.0.0.0 --port 7863 --workers 1
















set -e
echo "Starting Infusion Solutions NL→SQL on port 7862..."
uvicorn ui_app:app --host 0.0.0.0 --port 7862 --workers 1
