const { build } = require("./package.json");

module.exports = {
  ...build,
  extends: null,
  mac: {
    ...build.mac,
    extraResources: ["arm64", "x64"].map((arch) => ({
      from: `../build/bridge/${arch}/m4-bridge`,
      to: `bridge/${arch}/m4-bridge`,
    })),
    // Both app slices carry identical copies. Skip lipo to preserve the
    // appended Python archives in these PyInstaller onefile executables.
    x64ArchFiles: "Contents/Resources/bridge/**/m4-bridge",
  },
};
