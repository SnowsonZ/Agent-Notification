#!/usr/bin/env python3
"""Build Agent Notification.app.

Default: developer bundle — Info.plist carries SessionManagerRoot and the app runs the
repository's scripts and venv in place. --standalone: release bundle — scripts, CLI,
zcode-focus and pure-Python deps are copied into Contents/Resources so the app runs
without a checkout (needs a python3 on the machine; see bin/session-manager).
"""

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--standalone", action="store_true", help="bundle scripts and deps into the app"
)
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
contents = root / "build/Agent Notification.app/Contents"
# 发布约束（AGENTS.md）：版本号两处同源，改版本只动这里。
BUNDLE_SHORT_VERSION = "0.7.5"
BUNDLE_VERSION = "23"
running = subprocess.run(
    ["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=True
).stdout.splitlines()
if str(contents / "MacOS/Agent Notification") in [line.strip() for line in running]:
    raise SystemExit("Quit Agent Notification before rebuilding its executable.")
(contents / "MacOS").mkdir(parents=True, exist_ok=True)
# 两种构建共用同一目录：先清掉上一次可能留下的自包含资源，保证开发包不带旧脚本。
for stale in ("scripts", "bin", "build", "pylib"):
    shutil.rmtree(contents / "Resources" / stale, ignore_errors=True)
(contents / "Resources").mkdir(parents=True, exist_ok=True)
if args.standalone:
    resources = contents / "Resources"
    shutil.copytree(
        root / "scripts",
        resources / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "probe_*.py"),
    )
    (resources / "bin").mkdir()
    shutil.copy2(root / "bin/session-manager", resources / "bin/session-manager")
    (resources / "build").mkdir()
    helper = root / "build/zcode-focus"
    if not helper.is_file():
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                str(root / "native/ZcodeFocus.swift"),
                "-o",
                str(helper),
            ],
            check=True,
        )
    shutil.copy2(helper, resources / "build/zcode-focus")
    # 纯 Python wheel，按最低支持 3.11 解析；--target 不碰当前环境。
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(resources / "pylib"),
            "--no-compile",
            "--only-binary=:all:",
            "--platform",
            "any",
            "--python-version",
            "3.11",
            "--implementation",
            "py",
            "-r",
            str(root / "requirements-standalone.txt"),
        ],
        check=True,
    )
    shutil.rmtree(resources / "pylib/bin", ignore_errors=True)
for icon in sorted((root / "native/agent-icons").iterdir()):
    shutil.copy2(icon, contents / "Resources" / icon.name)
generator = root / "build/generate-app-icon"
subprocess.run(
    [
        "xcrun",
        "swiftc",
        str(root / "native/GenerateAppIcon.swift"),
        "-o",
        str(generator),
    ],
    check=True,
)
iconset = root / "build/AppIcon.iconset"
iconset_dark = root / "build/AppIconDark.iconset"
subprocess.run([str(generator), str(iconset), "light"], check=True)
subprocess.run([str(generator), str(iconset_dark), "dark"], check=True)
subprocess.run(
    [
        "iconutil",
        "-c",
        "icns",
        str(iconset),
        "-o",
        str(contents / "Resources/AppIcon.icns"),
    ],
    check=True,
)
subprocess.run(
    [
        "iconutil",
        "-c",
        "icns",
        str(iconset_dark),
        "-o",
        str(contents / "Resources/AppIconDark.icns"),
    ],
    check=True,
)


def compile_liquid_glass_icon() -> bool:
    """macOS 26+ 分层图标：把 native/AppIcon.icon 编成 Resources/Assets.car。

    actool 只随 Xcode 26 提供（命令行工具没有），缺失时静默跳过，系统回退到 icns。
    """
    probe = subprocess.run(
        ["xcrun", "--find", "actool"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        print(
            "actool unavailable (needs Xcode 26); Liquid Glass icon skipped, icns fallback only"
        )
        return False
    (contents / "Resources/Assets.car").unlink(missing_ok=True)
    subprocess.run(
        [
            probe.stdout.strip(),
            str(root / "native/AppIcon.icon"),
            "--compile",
            str(contents / "Resources"),
            "--app-icon",
            "AppIcon",
            "--include-all-app-icons",
            "--enable-on-demand-resources",
            "NO",
            "--development-region",
            "en",
            "--target-device",
            "mac",
            "--platform",
            "macosx",
            "--minimum-deployment-target",
            "14.0",
            "--output-partial-info-plist",
            "/dev/null",
            "--output-format",
            "human-readable-text",
            "--warnings",
            "--errors",
        ],
        check=True,
    )
    if not (contents / "Resources/Assets.car").is_file():
        raise SystemExit("actool finished without producing Assets.car")
    return True


def build_widgets() -> bool:
    """组件 appex（desktop-widgets.md §6）：构建 + plist + entitlements + ad-hoc 签名。

    返回 True = 配置式（appintentsmetadataprocessor 提取 Metadata.appintents 成功）；
    False = 降级静态组件（工具缺失，W8 的本机形态）。命令行与探针结论一致：
    docs/research/2026-09-23-widget-adhoc-probe.md 与 D0 探针记录。
    """
    processor = subprocess.run(
        ["xcrun", "--find", "appintentsmetadataprocessor"],
        capture_output=True,
        text=True,
        check=False,
    )
    configured = processor.returncode == 0
    sdk_root = subprocess.run(
        ["xcrun", "-sdk", "macosx", "-show-sdk-path"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    appex = contents / "PlugIns/Agent Notification Widgets.appex"
    shutil.rmtree(appex, ignore_errors=True)  # 两种形态共用目录：清掉上一轮产物
    exe_dir = appex / "Contents/MacOS"
    resources = appex / "Contents/Resources"
    exe_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    sources = sorted(str(path) for path in (root / "native/Widget").glob("*.swift"))
    sources += sorted(str(path) for path in (root / "native/Shared").glob("*.swift"))
    module = "AgentNotificationWidgets"
    target = "arm64-apple-macosx14.0"
    common = [
        "xcrun",
        "swiftc",
        "-module-name",
        module,
        "-parse-as-library",
        "-application-extension",
        "-target",
        target,
        "-sdk",
        sdk_root,
    ]
    # 多文件 -c 不能带 -o：在 exe_dir 内编译，产物（每源文件一个 .o）就地留下。
    objects = [exe_dir / (Path(source).stem + ".o") for source in sources]
    if configured:
        # 条件编译 + 常量值导出（AppIntents 元数据提取输入）
        subprocess.run(
            common
            + [
                "-D",
                "WIDGET_APPINTENTS",
                "-enable-testing",
                "-emit-const-values",
                "-Xfrontend",
                "-serialize-debugging-options",
                "-c",
                *sources,
            ],
            check=True,
            cwd=exe_dir,
        )
    else:
        print(
            "appintentsmetadataprocessor unavailable (needs Xcode); building static fallback widgets"
        )
        subprocess.run(common + ["-c", *sources], check=True, cwd=exe_dir)
    # 入口必须 _NSExtensionMain（探针：缺了组件一被拉起就崩溃）
    subprocess.run(
        common
        + [
            "-Xlinker",
            "-e",
            "-Xlinker",
            "_NSExtensionMain",
            "-framework",
            "WidgetKit",
            "-framework",
            "SwiftUI",
            "-framework",
            "AppKit",
            *[obj.name for obj in objects],
            "-o",
            module,
        ],
        check=True,
        cwd=exe_dir,
    )
    for obj in objects:
        obj.unlink()
    info = {
        "CFBundleDisplayName": "会话通知组件",
        "CFBundleExecutable": module,
        "CFBundleIdentifier": "local.session-manager.inbox.widgets",
        "CFBundleName": module,
        "CFBundlePackageType": "XPC!",
        "CFBundleShortVersionString": BUNDLE_SHORT_VERSION,
        "CFBundleVersion": BUNDLE_VERSION,
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleInfoDictionaryVersion": "7.0",
        "DTPlatformName": "macosx",
        "DTSDKName": "macosx14.0",
        "LSMinimumSystemVersion": "14.0",
        "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
    }
    (appex / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    # entitlements 只有两项：沙盒 + 快照目录只读例外（§6）。
    # 文件放 build 临时目录：放进 bundle 会被 codesign 当作未签名的子组件拒绝。
    entitlements = root / "build/AgentNotificationWidgets.entitlements"
    entitlements.write_bytes(
        plistlib.dumps(
            {
                "com.apple.security.app-sandbox": True,
                "com.apple.security.temporary-exception.files.home-relative-path.read-only": [
                    "/.local/state/session-manager/widget/"
                ],
            }
        )
    )
    if configured:
        toolchain_dir = str(Path(processor.stdout.strip()).parents[2])
        xcode_build = subprocess.run(
            ["xcodebuild", "-version"], capture_output=True, text=True, check=False
        )
        build_number = ""
        for line in xcode_build.stdout.splitlines():
            if line.startswith("Build version"):
                build_number = line.split()[-1]
        if build_number:
            constvals = sorted(Path(exe_dir).glob("*.constvals"))
            listing = resources / "constvals.txt"
            listing.write_text("".join(str(path) + chr(10) for path in constvals))
            source_list = resources / "sources.txt"
            source_list.write_text("".join(str(path) + chr(10) for path in sources))
            subprocess.run(
                [
                    processor.stdout.strip(),
                    "--output",
                    str(resources / "Metadata.appintents"),
                    "--toolchain-dir",
                    toolchain_dir,
                    "--module-name",
                    module,
                    "--sdk-root",
                    sdk_root,
                    "--xcode-version",
                    build_number,
                    "--platform-family",
                    "macos",
                    "--deployment-target",
                    "14.0",
                    "--target-triple",
                    target,
                    "--source-file-list",
                    str(source_list),
                    "--swift-const-vals-list",
                    str(listing),
                ],
                check=True,
            )
            listing.unlink()
            source_list.unlink()
        else:
            configured = False
            print("xcodebuild -version unavailable; falling back to static widgets")
    if configured and not (resources / "Metadata.appintents").is_file():
        configured = False
        print(
            "Metadata.appintents missing after extraction; treating build as static fallback"
        )
    subprocess.run(
        [
            "codesign",
            "--force",
            "--sign",
            "-",
            "--entitlements",
            str(entitlements),
            str(appex),
        ],
        check=True,
    )
    return configured


liquid_glass_icon = compile_liquid_glass_icon()
widgets_configured = build_widgets()
# native/ 下除两个独立工具外全部编进主程序；新增 Swift 文件自动纳入，无需改这里。
# Shared/ 与 Widget/ 是组件代码（Widget/ 的 WidgetBundle 带 @main，不能编进主程序），
# Shared/ 为两者共用的数据结构，显式加入主程序列表。
app_sources = sorted(
    str(path)
    for path in (root / "native").glob("*.swift")
    if path.name not in ("GenerateAppIcon.swift", "ZcodeFocus.swift")
) + sorted(str(path) for path in (root / "native/Shared").glob("*.swift"))
subprocess.run(
    [
        "xcrun",
        "swiftc",
        "-parse-as-library",
        "-target",
        "arm64-apple-macosx14.0",
        *app_sources,
        "-o",
        str(contents / "MacOS/Agent Notification"),
    ],
    check=True,
)
info = {
    "CFBundleExecutable": "Agent Notification",
    "CFBundleIdentifier": "local.session-manager.inbox",
    # 可执行文件与 bundle 目录名保留英文（路径稳定），用户可见名称为中文。
    "CFBundleName": "会话通知",
    "CFBundleDisplayName": "会话通知",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": BUNDLE_SHORT_VERSION,
    "CFBundleVersion": BUNDLE_VERSION,
    "LSMinimumSystemVersion": "14.0",
    "CFBundleIconFile": "AppIcon.icns",
    "NSHighResolutionCapable": True,
    "NSAppleEventsUsageDescription": "用于定位 iTerm2 中已有的 agent 会话，不向终端输入命令。",
    # 组件点击唤起主 App（desktop-widgets.md §5）
    "CFBundleURLTypes": [
        {
            "CFBundleURLName": "local.session-manager.inbox",
            "CFBundleURLSchemes": ["agentnotification"],
        }
    ],
}
if liquid_glass_icon:
    info["CFBundleIconName"] = (
        "AppIcon"  # macOS 26+ 读 Assets.car 分层图标，旧系统仍用 CFBundleIconFile
    )
if not args.standalone:
    info["SessionManagerRoot"] = str(root)  # 开发包：运行仓库内的脚本与 venv
(contents / "Info.plist").write_bytes(plistlib.dumps(info))
if not (contents / "PlugIns").is_dir():
    raise SystemExit("widget appex missing from bundle")
subprocess.run(["codesign", "--force", "--sign", "-", str(contents.parent)], check=True)
subprocess.run(
    ["codesign", "--verify", "--deep", "--strict", str(contents.parent)], check=True
)
# Refresh this bundle's metadata after in-place builds (name and icon changes).
contents.parent.touch()
subprocess.run(
    [
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
        "-f",
        str(contents.parent),
    ],
    check=True,
)
print(contents.parent)
