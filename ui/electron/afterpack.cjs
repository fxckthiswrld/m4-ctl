// macOS: ad-hoc deep re-sign of the packaged app.
//
// The app is distributed unsigned-by-identity (no Developer ID), so
// electron-builder seals it ad-hoc. The bridge's PyInstaller onedir tree
// adds nested Mach-O binaries that are not signed by the default pass;
// an invalid or incomplete seal makes macOS TCC treat the Bluetooth
// permission as unowned (it re-prompts on every launch and IOBluetooth
// returns empty results). A deep ad-hoc sign after packing gives the
// bundle a stable cdhash for the lifetime of the installation.
const { execSync } = require("node:child_process");
const path = require("node:path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  console.log(`Ad-hoc deep codesign: ${appPath}`);
  execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: "inherit" });
  const verify = execSync(`codesign --verify --deep --strict "${appPath}" 2>&1`, {
    encoding: "utf8",
  });
  console.log(`Codesign verify: ${verify || "ok"}`);
};
