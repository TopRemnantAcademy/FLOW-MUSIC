#!/usr/bin/env bash
# FLOW CCM Android 构建统一入口。
#
# 为什么需要这层包装（2026-09-30 实测，勿删）：
#   Capacitor 8 / AGP 8.13 编译要求 **JDK 21**；JDK 17 直接失败：
#     > error: invalid source release: 21
#   而本机 ~/.gradle/gradle.properties 存在 **全局** org.gradle.java.home=JDK17
#   （FLOW 主 App 的 Gradle 8.2.1 不支持 JDK 21，必须保留），全局优先于项目级，
#   所以项目内 gradle.properties 写 java.home 是无效的。
#   → 唯一可靠方式是命令行 -D 覆盖（优先级最高）。
#
# 用法：
#   bash scripts/build-android.sh assembleDebug      # 调试 APK
#   bash scripts/build-android.sh bundleRelease      # 发布 AAB（提交 Play 用）
#   bash scripts/build-android.sh assembleRelease    # 发布 APK（侧载/自测用）
#
# 换机器时用环境变量覆盖 JDK 路径：
#   CCM_JDK21=/path/to/jdk21 bash scripts/build-android.sh bundleRelease
set -euo pipefail

JDK21="${CCM_JDK21:-C:/Users/piaoy/.workbuddy/binaries/java/jdk21/jdk-21.0.12.1+1}"

if [ ! -x "$JDK21/bin/javac.exe" ] && [ ! -x "$JDK21/bin/javac" ]; then
  echo "❌ 未找到 JDK 21: $JDK21" >&2
  echo "   请用 CCM_JDK21=<jdk21 根目录> 指定。" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/../android"

echo "▶ JDK21 = $JDK21"
exec ./gradlew "$@" -Dorg.gradle.java.home="$JDK21"
