# FLOW CCM 移动端第三方软件声明（Android）

> 生成日期：2026-09-30 · 对应构建 `flow-ccm-v1.0.0-c1`（versionCode 1 / applicationId `com.flowccm.app`）
> 用途：Google Play 上架资料、合规审计、后续法务复核。
> 说明：本文件由 `native/package.json` + `android/app/build.gradle` 的实际依赖推导，**非模板**。

---

## 1. 运行时（打包进 APK/AAB）

| 组件 | 版本 | 许可证 | 用途 | 备注 |
|---|---|---|---|---|
| @capacitor/android | 8.5.2 | MIT | 原生桥接 | — |
| @capacitor/core | 8.5.2 | MIT | 核心运行时 | — |
| @capacitor/app | 8.1.1 | MIT | 返回键 / 深链 | — |
| @capacitor/haptics | 8.0.2 | MIT | 触感反馈 | — |
| @capacitor/keyboard | 8.0.5 | MIT | 键盘避让 | — |
| @capacitor/splash-screen | 8.0.2 | MIT | 启动图 | — |
| @capacitor/status-bar | 8.0.3 | MIT | 状态栏样式 | — |
| **@capgo/capacitor-native-audio** | **8.4.30** | **MPL-2.0** | 原生音频播放 / 锁屏媒体控制 | ⚠️ 见 §3 |
| androidx.appcompat | 1.7.1 | Apache-2.0 | UI 基础库 | 传递依赖 |
| androidx.coordinatorlayout | 1.3.0 | Apache-2.0 | 布局 | 传递依赖 |
| androidx.core:core-splashscreen | 1.2.0 | Apache-2.0 | 启动屏 | 传递依赖 |
| androidx.media3（exoplayer / session / hls） | 由 native-audio 引入 | Apache-2.0 | 媒体播放引擎 | 传递依赖，含 HLS 支持（+~4MB） |

**编译期（不入包）**：@capacitor/cli 8.5.2（MIT）、Gradle/AGP（Apache-2.0）。

---

## 2. Web 层（打包进 `assets/public`）

| 组件 | 版本 | 许可证 |
|---|---|---|
| react / react-dom | 18.3.1 | MIT |
| next | 16.3.6 | MIT |
| qrcode.react | ^3.1.0 | ISC |
| tailwindcss | 3.4.6 | MIT |

> 完整清单以 `frontend/package-lock.json` 为准；本表仅列直接依赖。

---

## 3. ⚠️ 需法务复核：MPL-2.0 组件

`@capgo/capacitor-native-audio` 采用 **Mozilla Public License 2.0**（弱 copyleft）。

- **MPL-2.0 的边界**：copyleft 仅覆盖**该库自身的源文件**及其修改版；**不传染**到调用它的应用代码。
  因此本应用（`com.flowccm.app`）**不需要**开源自身代码，闭源分发**是允许的**。
- **需履行的义务**：
  1. 保留版权与许可证声明（本文件 + 应用内"开源许可"页）；
  2. 若**修改了该库的源文件**，修改部分须以 MPL-2.0 公开。**当前未做任何修改**（仅通过 npm 安装使用）。
  3. 不得用其他许可证再授权该库本身。
- **为何仍写在此处**：若后续需要在应用内提供"开源许可"页，必须包含该库的完整 MPL-2.0 文本
  （`node_modules/@capgo/capacitor-native-audio/LICENSE`）。

> 备选方案（若法务倾向纯宽松许可）：`@capacitor-community/native-audio`（MIT）。
> 但**实测**其 `dist/esm/definitions.d.ts` 不含 `showNotification` / `backgroundPlayback` /
> `notificationMetadata`，即**缺少锁屏与通知栏媒体控制**，无法满足音乐类应用的核心体验，
> 故未采用。取舍记录见 `docs/PLAN_MOBILE_CROSSPLATFORM_2026-09-29.md`。

---

## 4. Google Play 上架需同步填写

| 项目 | 值 / 来源 |
|---|---|
| 应用包名 | `com.flowccm.app` |
| 版本 | versionCode **1** / versionName **1.0.0** |
| targetSdk | **36**（满足 Play 2026-08-31 起的要求） |
| minSdk | **24** |
| 签名证书 SHA-256 | `FE:EB:C6:62:41:4F:1D:42:D6:1D:A8:FE:57:6D:F1:E7:EF:37:50:B7:0D:93:D7:06:60:58:14:6B:69:72:D9:45` |
| 签名证书 SHA-1 | `9F:78:1E:0A:99:17:38:9B:21:63:19:8D:CA:3A:D1:9E:93:AB:38:0A` |
| 签名证书 MD5 | `31:4E:30:19:B2:84:34:83:D6:70:DF:52:D6:77:17:77` |
| 目标 API 等级（Play 后台） | API 36 |
| 数据安全表单 | 需据实际采集项填写（尚未完成） |
| 内容分级 | 含 UGC（广场）→ 须申报用户生成内容 |

> ⚠️ 若启用 **Play App Signing**（推荐），Google 会用自己的密钥重签，上面的 SHA-256 是
> **上传密钥**指纹；上架后台看到的**应用签名密钥**指纹会不同。两者都要留存。
