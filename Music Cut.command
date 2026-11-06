#!/bin/bash
set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  osascript -e 'display alert "Music Cut 尚未安装" message "请先双击 setup.command 完成安装。" as critical' 2>/dev/null || true
  echo "请先双击 setup.command 完成安装。"
  exit 1
fi
if [ ! -f "models/demucs/5c90dfd2-34c22ccb.th" ]; then
  echo "模型未就绪，正在准备…"
  ".venv/bin/python" -m musiccut.model_store || {
    echo "模型准备失败，请查看上方错误。"
    exit 1
  }
fi
exec ".venv/bin/python" -m musiccut.gui "$@"
