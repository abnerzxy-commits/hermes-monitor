#!/bin/bash
# 啟動專用的 Chrome instance（隔離 profile）給 Hermes 自動加購使用
# 第一次跑會是空白的 — 你需要在裡面登入 hermes.com 一次
# 之後 cookies 會持久保存，不用再登入

set -e

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEBUG_PORT=9222
PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome-Hermes-Debug"

# Chrome 必須能用 remote debugging，這需要獨立的 user-data-dir
mkdir -p "$PROFILE_DIR"

# 檢查是否已經有 Chrome debug 在跑
if curl -s "http://localhost:$DEBUG_PORT/json/version" > /dev/null 2>&1; then
  echo "✅ Chrome debug 已經在執行（port $DEBUG_PORT）"
  curl -s "http://localhost:$DEBUG_PORT/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"   {d.get('Browser','?')}\")"
  exit 0
fi

# 檢查 port 是否被其他東西佔用
if lsof -ti:$DEBUG_PORT > /dev/null 2>&1; then
  echo "❌ Port $DEBUG_PORT 被其他程序佔用"
  lsof -ti:$DEBUG_PORT
  exit 1
fi

echo "🚀 啟動 Chrome (Hermes 專用 instance)..."
echo "   Port: $DEBUG_PORT"
echo "   Profile: $PROFILE_DIR"
echo ""

# 用 nohup 在背景啟動，獨立 profile（必要條件）
nohup "$CHROME" \
  --remote-debugging-port=$DEBUG_PORT \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=ChromeWhatsNewUI \
  > /tmp/chrome_hermes_debug.log 2>&1 &

# 等 port 開放
echo "   等待 Chrome 啟動..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 1
  if curl -s "http://localhost:$DEBUG_PORT/json/version" > /dev/null 2>&1; then
    echo ""
    echo "✅ Chrome 已啟動！"
    curl -s "http://localhost:$DEBUG_PORT/json/version" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'   {d.get(\"Browser\",\"?\")}')"
    echo ""
    echo "📋 接下來："
    echo "   1. Chrome 視窗已開啟（這是 Hermes 專用 profile，第一次是空白的）"
    echo "   2. 在這個 Chrome 打開 https://www.hermes.com/tw/zh/"
    echo "   3. 登入你的愛馬仕帳號"
    echo "   4. 不要關閉這個 Chrome（它會持續跑在背景）"
    echo "   5. 之後 auto_buy.py 會用這個 Chrome 自動加購"
    echo ""
    echo "🧪 設定完登入後測試："
    echo "   python3 auto_buy.py --sku 084948CP89 --use-chrome --headed"
    exit 0
  fi
done

echo ""
echo "❌ Chrome 啟動失敗，看 /tmp/chrome_hermes_debug.log"
tail -10 /tmp/chrome_hermes_debug.log
exit 1
