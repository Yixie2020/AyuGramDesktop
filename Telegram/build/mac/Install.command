#!/bin/bash
# AyuGram 安装助手
# 用法：把 AyuGram.app 拖到「应用程序」文件夹后，双击此脚本
#
# 解决的问题：macOS Gatekeeper 会拦截未签名/未公证的应用，
# 弹窗提示"AyuGram 已损坏"。运行此脚本会清除 quarantine 标记，
# 让 macOS 允许启动应用。

set -e

APP_NAME="AyuGram"
APP_PATH="/Applications/${APP_NAME}.app"

# 让 Finder 显示此脚本所在的目录（双击 .command 时定位到 dmg）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------- 1. 检测应用是否已安装 ----------
FOUND_PATH=""

if [ -d "$APP_PATH" ]; then
    FOUND_PATH="$APP_PATH"
elif [ -d "$HOME/Applications/${APP_NAME}.app" ]; then
    FOUND_PATH="$HOME/Applications/${APP_NAME}.app"
elif [ -d "$SCRIPT_DIR/${APP_NAME}.app" ]; then
    FOUND_PATH="$SCRIPT_DIR/${APP_NAME}.app"
fi

if [ -z "$FOUND_PATH" ]; then
    osascript <<EOF
display dialog "未找到 ${APP_NAME}.app

请先把这个 dmg 里的 ${APP_NAME}.app 拖到��应用程序」文件夹，
然后再双击此脚本。" buttons {"好的"} default button 1 with icon caution with title "${APP_NAME} 安装助手"
EOF
    echo "❌ 未找到 ${APP_NAME}.app，请先把它拖到「应用程序」。"
    read -p "按回车关闭..."
    exit 1
fi

# ---------- 2. 清除 quarantine 标记 + 重新签名 ----------
echo "正在修复 ${FOUND_PATH} ..."
xattr -cr "$FOUND_PATH"
echo "  ✅ 已清除隔离属性。"

# 给 ad-hoc 签名补回 Info.plist 绑定和资源密封
# （未做 Apple Developer ID 签名时，ld 的 linker-signed adhoc 会让
#  macOS 启动后秒退，这里重新 codesign 一次修复）
codesign --force --deep --sign - "$FOUND_PATH" > /dev/null 2>&1
if codesign -dv "$FOUND_PATH" 2>&1 | grep -q "Info.plist entries="; then
    echo "  ✅ 已重新签名。"
else
    echo "  ⚠️  重新签名失败，可能缺少 codesign 工具。"
fi

# ---------- 3. 询问是否启动 ----------
CHOICE=$(osascript <<EOF
try
    button returned of (display dialog "${APP_NAME} 已就绪！

是否现在打开？" buttons {"打开 ${APP_NAME}", "稍后"} default button 1 with icon note with title "${APP_NAME} 安装助手")
on error
    return "稍后"
end try
EOF
) || CHOICE="稍后"

if [ "$CHOICE" = "打开 ${APP_NAME}" ]; then
    open "$FOUND_PATH"
    echo "🚀 已启动 ${APP_NAME}"
fi

echo ""
echo "完成。可以关闭此窗口了。"
read -p "按回车关闭..."
exit 0
