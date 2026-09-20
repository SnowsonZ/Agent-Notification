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


liquid_glass_icon = compile_liquid_glass_icon()
# native/ 下除两个独立工具外全部编进主程序；新增 Swift 文件自动纳入，无需改这里。
app_sources = sorted(
    str(path)
    for path in (root / "native").glob("*.swift")
    if path.name not in ("GenerateAppIcon.swift", "ZcodeFocus.swift")
)
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
    "CFBundleShortVersionString": "0.7.5",
    "CFBundleVersion": "23",
    "LSMinimumSystemVersion": "14.0",
    "CFBundleIconFile": "AppIcon.icns",
    "NSHighResolutionCapable": True,
    "NSAppleEventsUsageDescription": "用于定位 iTerm2 中已有的 agent 会话，不向终端输入命令。",
}
if liquid_glass_icon:
    info["CFBundleIconName"] = (
        "AppIcon"  # macOS 26+ 读 Assets.car 分层图标，旧系统仍用 CFBundleIconFile
    )
if not args.standalone:
    info["SessionManagerRoot"] = str(root)  # 开发包：运行仓库内的脚本与 venv
(contents / "Info.plist").write_bytes(plistlib.dumps(info))
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
