#!/usr/bin/env python3
import json
import os
import re
import shutil
import urllib.request
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from subprocess import check_call, check_output, list2cmdline
from typing import Iterable, Required, Optional, Any
from typing import TypedDict
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

BUILD_RELEASE = json.loads(os.environ.get("BUILD_RELEASE", "false"))

ROOT_PATH = Path(__file__).parent
PROJECT_PATH = ROOT_PATH / "OpenEUICC"
VARIANTS_PATH = ROOT_PATH / "variants.json"
KEYSTORE_PATH = ROOT_PATH / "keystore"
ARTIFACT_PATH = ROOT_PATH / "artifacts"


class Variant(TypedDict):
    name: Required[str]
    variant: Required[str]
    flavor: Optional[str]
    branch_pattern: Required[str]
    output_file: Required[str]
    sign_keys: Required[list[str]]


class VersionInfo(TypedDict):
    version_code: Required[int]
    version_name: Required[str]
    package_name: Required[str]


class AndroidSigner:
    apksigner_path = next(Path(os.environ["ANDROID_HOME"]).rglob("apksigner"))

    def __init__(self, base_path: Path):
        self.base_path = base_path

    def __call__(self, apk_path: Path, sign_keys: list[str]):
        # https://developer.android.com/tools/apksigner

        def build_args() -> Iterable[str]:
            yield "--v1-signing-enabled=true"
            yield "--v2-signing-enabled=true"
            yield "--v3-signing-enabled=false"
            yield "--v4-signing-enabled=false"
            for index, filename in enumerate(sign_keys):
                keystore_path = self.base_path / filename
                if index > 0:
                    yield "--next-signer"
                yield f"--ks={keystore_path}"
                yield f"--ks-pass=file:{keystore_path.with_suffix('.txt')}"

        print("Signing APK:", apk_path)
        check_call([str(self.apksigner_path), "sign", *build_args(), str(apk_path)])
        print("Print Certificate from APK:", apk_path)
        check_call([str(self.apksigner_path), "verify", "--print-certs", str(apk_path)])


class Project:
    base_path: Path
    build_type = "release" if BUILD_RELEASE else "debug"

    def __init__(self, base_path: Path):
        self.base_path = base_path

    def clean(self):
        self.invoke_gradle("clean")

    def build(self, variant: str, flavor: Optional[str] = None):
        self.invoke_gradle(f":{variant}:assemble{flavor or ''}{self.build_type.title()}")
        return self.get_output_path(variant, flavor)

    def invoke_gradle(self, task: str):
        cmdline = ["gradlew", task]
        print("$", list2cmdline(cmdline))
        cmdline[0] = str(self.base_path / cmdline[0])
        check_call(cmdline, cwd=self.base_path)

    def get_output_path(self, app_name: str, flavor: Optional[str] = None) -> Path:
        parts = [app_name, flavor, self.build_type, "unsigned" if BUILD_RELEASE else None]
        filename = "-".join(filter(None.__ne__, parts))
        return next(self.base_path.rglob(f"{filename}.apk"), None)

    @property
    def branch_name(self) -> str:
        return check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.base_path) \
            .decode("utf-8").strip()


def get_file_content(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:
        return response.read()


def get_version_info(apk_path: Path) -> VersionInfo:
    program = next(Path(os.environ["ANDROID_HOME"]).rglob("aapt"))
    output = check_output([program, "dump", "badging", str(apk_path)]).decode("utf-8")
    package_name = re.search(r"name='(?P<name>[^']+)'", output)
    version_name = re.search(r"versionName='(?P<name>[^']+)'", output)
    version_code = re.search(r"versionCode='(?P<code>[^']+)'", output)
    return {
        "package_name": package_name.group("name"),
        "version_name": version_name.group("name"),
        "version_code": int(version_code.group("code")),
    }


def build_module_prop(version: VersionInfo):
    module: dict[str, Any] = {
        "id": "openeuicc-sekaiacg",
        "name": "OpenEUICC",
        "version": version["version_name"],
        "versionCode": version["version_code"],
        "author": f"Peter Cai, Modifier by @sekaiacg",
        "description": " ".join([
            "OpenEUICC provides system-level eSIM integration.",
            f"Source Code: https://gitea.angry.im/PeterCxy/OpenEUICC.",
            f"Magisk Module: https://github.com/sekaiacg/magisk-openeuicc."
        ]),
    }
    return "".join(
        f"{name}={value}\n"
        for name, value in module.items()
    )


def build_magisk_module(base_path: Path, project_path: Path, bundle_path: Path, apk_path: Path) -> None:
    print("Building Magisk module at", bundle_path)

    module_installer_url = "https://github.com/topjohnwu/Magisk/raw/bf4ed29/scripts/module_installer.sh"

    bundle_path.unlink(missing_ok=True)
    version_info = get_version_info(apk_path)

    customize_script_path = base_path / "customize.sh"
    whitelist_path = project_path / "privapp_whitelist_im.angry.openeuicc.xml"

    meta_info_path = Path("META-INF") / "com" / "google" / "android"
    system_ext_path = Path("system") / "system_ext"
    app_path = system_ext_path / "priv-app" / "OpenEUICC" / "OpenEUICC.apk"
    perms_path = system_ext_path / "etc" / "permissions" / f"privapp_whitelist_{version_info['package_name']}.xml"

    with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as zip_file:
        zip_file.writestr(str(meta_info_path / "update-binary"), get_file_content(module_installer_url))
        zip_file.writestr(str(meta_info_path / "updater-script"), "#MAGISK\n", compress_type=ZIP_STORED)

        zip_file.writestr(str(app_path), apk_path.read_bytes(), compress_type=ZIP_STORED)
        zip_file.writestr(str(perms_path), whitelist_path.read_bytes())

        zip_file.writestr("customize.sh", customize_script_path.read_text().format(
            PKG_NAME=version_info["package_name"],
            APK_PATH=app_path,
            APK_NAME=os.path.basename(app_path)
        ))
        zip_file.writestr("uninstall.sh", list2cmdline(["pm", "uninstall", version_info['package_name']]))
        zip_file.writestr("module.prop", build_module_prop(version_info))


def setup_build_keystore():
    keystore = os.environ.get("BUILD_KEYSTORE_ZIPPED")
    password = os.environ.get("BUILD_KEYSTORE_PASSWORD")
    if not keystore or not password:
        raise ValueError("Environment variables BUILD_KEYSTORE_{ZIPPED,PASSWORD} must be set.")
    with ZipFile(BytesIO(b64decode(keystore)), "r") as zip_file:
        zip_file.setpassword(password.encode("utf-8"))
        zip_file.extractall(KEYSTORE_PATH)


def load_variants() -> list[Variant]:
    if not VARIANTS_PATH.exists():
        raise FileNotFoundError(f"Variants file not found: {VARIANTS_PATH}")
    return json.loads(VARIANTS_PATH.read_text())


def main():
    signer = AndroidSigner(KEYSTORE_PATH)
    project = Project(PROJECT_PATH)
    branch = project.branch_name
    project.clean()
    ARTIFACT_PATH.mkdir(parents=True, exist_ok=True)
    for variant in load_variants():
        if not re.match(variant["branch_pattern"], branch):
            continue
        print("Building Variant:", repr(variant["name"]))
        build_output_file = project.build(variant["variant"], variant.get("flavor"))
        store_output_file = ARTIFACT_PATH / variant["output_file"]
        shutil.copyfile(str(build_output_file), str(store_output_file))
        signer(store_output_file, variant["sign_keys"])
        if variant["variant"] == "app":
            build_magisk_module(
                ROOT_PATH / "magisk-module",
                PROJECT_PATH,
                ARTIFACT_PATH / "magisk-module.zip",
                store_output_file,
            )


if __name__ == "__main__":
    setup_build_keystore()
    main()
