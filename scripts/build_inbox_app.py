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


def _write_widget_project(
    project_dir: Path, sources: list[Path], info_plist: Path
) -> None:
    """生成最小 widget appex 工程（D0 探针结论：手动 swiftc + processor 在
    Xcode 26/Swift 6.3 上产不出 const values，只有 xcodebuild 的构建系统会
    以正确参数产出 .swiftconstvalues 并驱动 appintentsmetadataprocessor）。"""
    file_refs, build_files, source_paths, source_children = [], [], [], []
    for index, source in enumerate(sources):
        ref, build = f"F1{index:04d}", f"B1{index:04d}"
        file_refs.append(
            f"\t\t{ref} /* {source.name} */ = {{isa = PBXFileReference; "
            f'lastKnownFileType = sourcecode.swift; path = "{source}"; '
            f'sourceTree = "<absolute>"; }};'
        )
        build_files.append(
            f"\t\t{build} /* {source.name} in Sources */ = {{isa = PBXBuildFile; "
            f"fileRef = {ref}; }};"
        )
        source_paths.append(f"\t\t\t\t{build},")
        source_children.append(f"\t\t\t\t{ref},")
    pbx = (
        """// !$*UTF8*$!
    {
    \tarchiveVersion = 1;
    \tclasses = {
    \t};
    \tobjectVersion = 56;
    \tobjects = {

    /* Begin PBXBuildFile section */
    """
        + "\n".join(build_files)
        + """
    /* End PBXBuildFile section */

    /* Begin PBXFileReference section */
    """
        + "\n".join(file_refs)
        + """
    \t\tF10000 /* Info.plist */ = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = "PLACEHOLDER_PLIST"; sourceTree = "<absolute>"; };
    \t\tF19999 /* AgentNotificationWidgets.appex */ = {isa = PBXFileReference; explicitFileType = "wrapper.app-extension"; includeInIndex = 0; path = AgentNotificationWidgets.appex; sourceTree = BUILT_PRODUCTS_DIR; };
    /* End PBXFileReference section */

    /* Begin PBXFrameworksBuildPhase section */
    P10002 /* Frameworks */ = {
    \t\tisa = PBXFrameworksBuildPhase;
    \t\tbuildActionMask = 2147483647;
    \t\tfiles = (
    \t\t);
    \t\trunOnlyForDeploymentPostprocessing = 0;
    \t};
    /* End PBXFrameworksBuildPhase section */

    /* Begin PBXGroup section */
    G10001 = {
    \t\tisa = PBXGroup;
    \t\tchildren = (
    \t\t\t\tG10002,
    \t\t\t\tF19999,
    \t\t);
    \t\tsourceTree = "<group>";
    \t};
    G10002 = {
    \t\tisa = PBXGroup;
    \t\tchildren = (
    """
        + "\n".join(source_children)
        + """
    \t\t\t\tF10000,
    \t\t);
    \t\tname = Sources;
    \t\tsourceTree = "<group>";
    \t};
    /* End PBXGroup section */

    /* Begin PBXNativeTarget section */
    T10001 /* AgentNotificationWidgets */ = {
    \t\tisa = PBXNativeTarget;
    \t\tbuildConfigurationList = C10001;
    \t\tbuildPhases = (
    \t\t\t\tP10001,
    \t\t\t\tP10002,
    \t\t);
    \t\tdependencies = (
    \t\t);
    \t\tname = AgentNotificationWidgets;
    \t\tproductName = AgentNotificationWidgets;
    \t\tproductReference = F19999;
    \t\tproductType = "com.apple.product-type.app-extension";
    \t};
    /* End PBXNativeTarget section */

    /* Begin PBXProject section */
    PR1001 /* Project object */ = {
    \t\tisa = PBXProject;
    \t\tattributes = {
    \t\t\t\tLastSwiftUpdateCheck = 1600;
    \t\t\t\tLastUpgradeCheck = 1600;
    \t\t};
    \t\tbuildConfigurationList = C10003;
    \t\tdevelopmentRegion = en;
    \t\tknownRegions = (
    \t\t\t\ten,
    \t\t\t\tBase,
    \t\t);
    \t\tmainGroup = G10001;
    \t\tproductRefGroup = G10001;
    \t\tprojectDirPath = "PLACEHOLDER_PROJECT";
    \t\tprojectRoot = "";
    \t\ttargets = (
    \t\t\t\tT10001,
    \t\t);
    \t};
    /* End PBXProject section */

    /* Begin PBXSourcesBuildPhase section */
    P10001 /* Sources */ = {
    \t\tisa = PBXSourcesBuildPhase;
    \t\tbuildActionMask = 2147483647;
    \t\tfiles = (
    """
        + "\n".join(source_paths)
        + """
    \t\t);
    \t\trunOnlyForDeploymentPostprocessing = 0;
    \t};
    /* End PBXSourcesBuildPhase section */

    /* Begin XCBuildConfiguration section */
    C10002 /* Release */ = {
    \t\tisa = XCBuildConfiguration;
    \t\tbuildSettings = {
    \t\t\t\tCODE_SIGNING_ALLOWED = NO;
    \t\t\t\tCODE_SIGN_IDENTITY = "";
    \t\t\t\tCURRENT_PROJECT_VERSION = BUNDLE_VERSION_PLACEHOLDER;
    \t\t\t\tGENERATE_INFOPLIST_FILE = NO;
    \t\t\t\tINFOPLIST_FILE = "PLACEHOLDER_PLIST";
    \t\t\t\tMARKETING_VERSION = "BUNDLE_SHORT_VERSION_PLACEHOLDER";
    \t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = local.session-manager.inbox.widgets;
    \t\t\t\tPRODUCT_NAME = "$(TARGET_NAME)";
    \t\t\t\tSDKROOT = macosx;
    \t\t\t\tSKIP_INSTALL = YES;
    \t\t\t\tSWIFT_ENABLE_EMIT_CONST_VALUES = YES;
    \t\t\t\tSWIFT_VERSION = 5.0;
    \t\t\t\tMACOSX_DEPLOYMENT_TARGET = 14.0;
    \t\t};
    \t\tname = Release;
    \t};
    C10001 /* Build configuration list for PBXNativeTarget */ = {
    \t\tisa = XCConfigurationList;
    \t\tbuildConfigurations = (
    \t\t\t\tC10002,
    \t\t);
    \t\tdefaultConfigurationIsVisible = 0;
    \t\tdefaultConfigurationName = Release;
    \t};
    C10004 /* Release */ = {
    \t\tisa = XCBuildConfiguration;
    \t\tbuildSettings = {
    \t\t};
    \t\tname = Release;
    \t};
    C10003 /* Build configuration list for PBXProject */ = {
    \t\tisa = XCConfigurationList;
    \t\tbuildConfigurations = (
    \t\t\t\tC10004,
    \t\t);
    \t\tdefaultConfigurationIsVisible = 0;
    \t\tdefaultConfigurationName = Release;
    \t};
    /* End XCBuildConfiguration section */
    \t};
    \trootObject = PR1001 /* Project object */;
    }
    """
    )
    pbx = (
        pbx.replace("PLACEHOLDER_PLIST", str(info_plist))
        .replace("PLACEHOLDER_PROJECT", str(project_dir))
        .replace("BUNDLE_VERSION_PLACEHOLDER", BUNDLE_VERSION)
        .replace("BUNDLE_SHORT_VERSION_PLACEHOLDER", BUNDLE_SHORT_VERSION)
    )
    (project_dir / "Widgets.xcodeproj").mkdir(parents=True, exist_ok=True)
    (project_dir / "Widgets.xcodeproj" / "project.pbxproj").write_text(pbx)


def build_widgets() -> bool:
    """组件 appex（desktop-widgets.md §6）：构建 + plist + entitlements + ad-hoc 签名。

    返回 True = 配置式（Metadata.appintents 提取成功）；False = 降级静态组件。
    D0 探针结论（2026-09-23）：手动 swiftc + appintentsmetadataprocessor 在
    Xcode 26/Swift 6.3 上无法产出 const values，配置式构建只能走 xcodebuild
    （执行计划预案 A：配置式组件只在 Xcode 环境构建）；CLT 环境走降级静态。
    """
    xcode = subprocess.run(
        ["xcodebuild", "-version"], capture_output=True, text=True, check=False
    )
    configured = xcode.returncode == 0
    appex = contents / "PlugIns/Agent Notification Widgets.appex"
    shutil.rmtree(appex, ignore_errors=True)  # 两种形态共用目录：清掉上一轮产物
    exe_dir = appex / "Contents/MacOS"
    resources = appex / "Contents/Resources"
    resources.mkdir(parents=True, exist_ok=True)
    module = "AgentNotificationWidgets"
    sources = sorted(str(path) for path in (root / "native/Widget").glob("*.swift"))
    sources += sorted(str(path) for path in (root / "native/Shared").glob("*.swift"))
    sources = [str(path) for path in sources]

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
        # 预案 A：xcodebuild 构建（build system 正确产出 const values 与元数据）。
        project_dir = root / "build/widgets-project"
        shutil.rmtree(project_dir, ignore_errors=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        info_plist = project_dir / "Info.plist"
        info_plist.write_bytes(
            plistlib.dumps(
                {
                    "CFBundleDisplayName": "会话通知组件",
                    "CFBundleExecutable": "$(EXECUTABLE_NAME)",
                    "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
                    "CFBundleName": module,
                    "CFBundlePackageType": "XPC!",
                    "CFBundleShortVersionString": BUNDLE_SHORT_VERSION,
                    "CFBundleVersion": BUNDLE_VERSION,
                    "CFBundleSupportedPlatforms": ["MacOSX"],
                    "LSMinimumSystemVersion": "14.0",
                    "NSExtension": {
                        "NSExtensionPointIdentifier": "com.apple.widgetkit-extension"
                    },
                }
            )
        )
        _write_widget_project(
            project_dir, [Path(source) for source in sources], info_plist
        )
        subprocess.run(
            [
                "xcodebuild",
                "-project",
                str(project_dir / "Widgets.xcodeproj"),
                "-scheme",
                "AgentNotificationWidgets",
                "-configuration",
                "Release",
                "-sdk",
                "macosx",
                "-derivedDataPath",
                str(root / "build/widgets-derived"),
                "CODE_SIGNING_ALLOWED=NO",
                "CODE_SIGN_IDENTITY=",
                "build",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        built = next(
            (root / "build/widgets-derived").rglob("AgentNotificationWidgets.appex")
        )
        shutil.rmtree(exe_dir, ignore_errors=True)
        shutil.copytree(built, appex)
        if not (resources / "Metadata.appintents").exists():
            configured = False
            print(
                "xcodebuild produced no Metadata.appintents; treating build as static fallback"
            )
    else:
        print(
            "xcodebuild unavailable (Command Line Tools only); building static fallback widgets"
        )
        exe_dir.mkdir(parents=True, exist_ok=True)
        target = "arm64-apple-macosx14.0"
        sdk_root = subprocess.run(
            ["xcrun", "-sdk", "macosx", "-show-sdk-path"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
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
        appex_info = appex / "Contents/Info.plist"
        appex_info.write_bytes(
            plistlib.dumps(
                {
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
                    "NSExtension": {
                        "NSExtensionPointIdentifier": "com.apple.widgetkit-extension"
                    },
                }
            )
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
