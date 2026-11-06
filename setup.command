#!/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

pause() {
  if [ -t 0 ]; then
    echo
    read -r -p "按回车键关闭此窗口…" _ || true
  fi
}
fail() {
  local code=$?
  echo
  echo "安装/检查失败（退出码 $code）。"
  echo "请检查网络、磁盘空间，或把上方错误信息保留下来。"
  pause
  exit "$code"
}
trap fail ERR

echo "Music Cut · 本地五轨分离"
echo "目录：$APP_DIR"
echo

find_uv() {
  local candidate
  if [ -n "${UV_BIN:-}" ] && [ -x "${UV_BIN:-}" ]; then
    echo "$UV_BIN"; return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv; return 0
  fi
  for candidate in "$HOME/.local/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv" "$APP_DIR/.tools/uv"; do
    if [ -x "$candidate" ]; then echo "$candidate"; return 0; fi
  done
  return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  echo "未找到 uv，正在把独立安装器下载到本工具的 .tools/ 目录…"
  mkdir -p ".tools"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$APP_DIR/.tools" sh
  UV="$APP_DIR/.tools/uv"
fi
if [ ! -x "$UV" ]; then
  echo "uv 安装失败：$UV"
  exit 1
fi
echo "uv：$UV"

export UV_CACHE_DIR="$APP_DIR/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$APP_DIR/.python"
echo
echo "正在校验/安装 Python 依赖（首次可能需要几分钟）…"
"$UV" sync --frozen

if [ ! -x ".venv/bin/python" ]; then
  echo "虚拟环境创建失败：.venv/bin/python 不存在。"
  exit 1
fi
echo
echo "正在校验分离模型…"
".venv/bin/python" -m musiccut.model_store

chmod +x "Music Cut.command" "bin/musiccut" 2>/dev/null || true
trap - ERR
echo
echo "完成。双击 Music Cut.app 启动图形界面。"
echo "命令行用法：./bin/musiccut 歌名.mp3"
pause
