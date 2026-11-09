const { build } = require("./package.json");

module.exports = {
  ...build,
  extends: null,
  mac: {
    ...build.mac,
    extraResources: ["arm64", "x64"].map((arch) => ({
      from: `../build/bridge/${arch}/`,
      to: `bridge/${arch}/`,
    })),
    // The whole onedir tree is arch-specific (launcher, dylibs, _internal
    // extension modules): each app slice keeps its own copy of the bridge.
    x64ArchFiles: "Contents/Resources/bridge/**",
  },
};
