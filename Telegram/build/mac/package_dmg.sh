#!/bin/bash
# AyuGram DMG 打包脚本
# 用法：
#   ./package_dmg.sh                     # 用默认路径打包
#   ./package_dmg.sh /path/to/AyuGram.app /path/to/output.dmg
#
# 工作流程：
#   1. 创建临时 dmg 镜像
#   2. 挂载，把 APP + Install.command 复制进去
#   3. 创建指向 /Applications 的别名
#   4. 卸载，压缩成只读 dmg

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DEFAULT_APP="$REPO_ROOT/out/AyuGram.app"
DEFAULT_OUTPUT="$REPO_ROOT/release/AyuGram.dmg"

APP_PATH="${1:-$DEFAULT_APP}"
OUTPUT_DMG="${2:-$DEFAULT_OUTPUT}"
INSTALL_CMD="$SCRIPT_DIR/Install.command"

# ---------- 1. 前置检查 ----------
if [ ! -d "$APP_PATH" ]; then
    echo "❌ 找不到 APP: $APP_PATH"
    echo "用法: $0 [/path/to/AyuGram.app] [/path/to/output.dmg]"
    exit 1
fi

if [ ! -f "$INSTALL_CMD" ]; then
    echo "❌ 找不到 Install.command: $INSTALL_CMD"
    exit 1
fi

APP_NAME=$(basename "$APP_PATH" .app)
OUTPUT_DIR=$(dirname "$OUTPUT_DMG")
mkdir -p "$OUTPUT_DIR"

# ---------- 2. 清理旧文件 ----------
rm -f "$OUTPUT_DMG"
TMP_DMG="$OUTPUT_DIR/.${APP_NAME}.tmp.dmg"
rm -f "$TMP_DMG"
# 先准备一个临时目录，把要打包的内容放进去，再用 srcfolder 模式创建
echo "📦 创建临时 dmg..."
DMG_STAGING=$(mktemp -d -t dmgstage)
cp -R "$APP_PATH" "$DMG_STAGING/"
cp "$INSTALL_CMD" "$DMG_STAGING/Install.command"
chmod +x "$DMG_STAGING/Install.command"
ln -sf /Applications "$DMG_STAGING/Applications"
DMG_SIZE_MB=$(du -sm "$APP_PATH" | awk '{print int($1 * 1.5) + 100}')
hdiutil create -srcfolder "$DMG_STAGING" -volname "${APP_NAME}" \
    -fs HFS+ -fsargs "-c c=64,a=16,e=16" -format UDRW \
    -size "${DMG_SIZE_MB}m" "$TMP_DMG"
rm -rf "$DMG_STAGING"

# ---------- 4. 压缩为只读 dmg ----------
echo "💿 压缩 dmg..."
hdiutil convert "$TMP_DMG" -format UDZO -ov -o "$OUTPUT_DMG"
rm -f "$TMP_DMG"

echo ""
echo "✅ 打包完成: $OUTPUT_DMG"
echo "📏 大小: $(du -h "$OUTPUT_DMG" | awk '{print $1}')"
echo ""
echo "用户使用步骤："
echo "  1. 双击 ${OUTPUT_DMG##*/} 挂载"
echo "  2. 把 ${APP_NAME}.app 拖到 Applications 别名"
echo "  3. 双击 \"${APP_NAME} 安装助手.command\" 清除隔离属性"
echo "  4. 在启动台打开 ${APP_NAME}"
