// macOS: sign the packaged app with a stable local identity.
//
// Bluetooth permissions (TCC) are keyed to the app's code-signing identity.
// An ad-hoc signature has no stable identity, so macOS re-prompts on every
// launch and IOBluetooth calls end up silently denied (empty paired-device
// lists, empty SDP results). Signing with a persistent self-signed code
// signing certificate gives the app a stable identity: the grant is stored
// once and survives relaunches and reinstalls signed with the same cert.
const { execSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

const CERT_NAME = "Momentum 4 Control Local";

function hasSigningIdentity() {
  try {
    const list = execSync("security find-identity -v -p codesigning", {
      encoding: "utf8",
    });
    return list.includes(CERT_NAME);
  } catch {
    return false;
  }
}

function ensureSigningIdentity() {
  if (hasSigningIdentity()) return CERT_NAME;
  console.log(`Creating local signing identity "${CERT_NAME}"`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "m4sign-"));
  const key = path.join(dir, "key.pem");
  const cert = path.join(dir, "cert.pem");
  execSync(
    `openssl req -x509 -newkey rsa:2048 -keyout "${key}" -out "${cert}" ` +
      `-days 3650 -nodes -subj "/CN=${CERT_NAME}" ` +
      `-addext "keyUsage=digitalSignature" ` +
      `-addext "extendedKeyUsage=codeSigning"`,
    { stdio: "ignore" }
  );
  // -T pre-authorizes codesign to use the key without a GUI prompt.
  execSync(`security import "${key}" -T /usr/bin/codesign`, { stdio: "ignore" });
  execSync(`security import "${cert}"`, { stdio: "ignore" });
  try {
    // User-domain trust; no sudo required. If it fails, the signature is
    // still valid for local runs and TCC identity remains stable.
    execSync(`security add-trusted-cert -p codeSign "${cert}"`, { stdio: "ignore" });
  } catch (error) {
    console.log("add-trusted-cert skipped:", String(error).slice(0, 120));
  }
  fs.rmSync(dir, { recursive: true, force: true });
  return CERT_NAME;
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  let identity = "-";
  try {
    identity = ensureSigningIdentity();
  } catch (error) {
    console.log("Signing identity unavailable, using ad-hoc:", String(error).slice(0, 120));
  }
  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  console.log(`Codesign (${identity}): ${appPath}`);
  execSync(`codesign --force --deep --sign "${identity}" "${appPath}"`, {
    stdio: "inherit",
  });
  execSync(`codesign --verify --deep --strict "${appPath}"`, { stdio: "inherit" });
  console.log("Codesign ok");
};
