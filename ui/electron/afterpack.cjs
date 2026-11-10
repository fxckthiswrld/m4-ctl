// macOS: sign the packaged app with a stable local identity.
//
// Bluetooth permissions (TCC) are keyed to the app's code-signing identity.
// An ad-hoc signature has no stable identity, so macOS re-prompts on every
// launch and IOBluetooth calls end up silently denied (empty paired-device
// lists, empty SDP results). Signing with a persistent self-signed code
// signing certificate gives the app a stable identity: the grant is stored
// once and survives relaunches and reinstalls signed with the same cert.
//
// Notes:
// - /usr/bin/openssl on macOS is LibreSSL and does not support -addext, so
//   the extensions are passed via a config file.
// - A dedicated keychain avoids GUI prompts: CI runners and SSH sessions
//   have no security agent to answer them.
const { execSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
const crypto = require("node:crypto");

const CERT_NAME = "Momentum 4 Control Local";
const KEYCHAIN = path.join(os.homedir(), "Library", "Keychains", "m4sign.keychain-db");

function keychainPassword() {
  const passFile = path.join(os.homedir(), "Library", "Keychains", "m4sign.pass");
  try {
    const stored = fs.readFileSync(passFile, "utf8").trim();
    if (stored) return stored;
  } catch {}
  const generated = "m4-" + crypto.randomBytes(16).toString("hex");
  fs.mkdirSync(path.dirname(passFile), { recursive: true });
  fs.writeFileSync(passFile, generated, { mode: 0o600 });
  return generated;
}

function ensureKeychain() {
  if (fs.existsSync(KEYCHAIN)) return;
  const pass = keychainPassword();
  console.log("Creating signing keychain: " + KEYCHAIN);
  execSync(`security create-keychain -p "${pass}" "${KEYCHAIN}"`);
  try {
    // Never auto-lock: signing must work over SSH and after reboots.
    execSync(`security set-keychain-settings -u "${KEYCHAIN}"`);
  } catch {}
  // Make sure codesign finds identities from this keychain.
  const current = execSync("security list-keychains -d keys", { encoding: "utf8" })
    .split("\n")
    .map((s) => s.trim().replace(/^"|"$/g, ""))
    .filter(Boolean);
  if (!current.includes(KEYCHAIN)) {
    execSync(
      `security list-keychains -s ${current.map((k) => JSON.stringify(k)).join(" ")} ${JSON.stringify(KEYCHAIN)}`
    );
  }
}

function certExists() {
  try {
    execSync(`security find-certificate -c "${CERT_NAME}"`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function ensureSigningCertificate() {
  ensureKeychain();
  if (certExists()) return;
  console.log(`Creating local code signing certificate "${CERT_NAME}"`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "m4sign-"));
  const key = path.join(dir, "key.pem");
  const cert = path.join(dir, "cert.pem");
  const cfg = path.join(dir, "cnf");
  fs.writeFileSync(
    cfg,
    [
      "[req]",
      "distinguished_name=dn",
      "x509_extensions=v3req",
      "[dn]",
      "[v3req]",
      "keyUsage=digitalSignature",
      "extendedKeyUsage=codeSigning",
    ].join("\n")
  );
  execSync(
    `openssl req -x509 -newkey rsa:2048 -keyout "${key}" -out "${cert}" ` +
      `-days 3650 -nodes -subj "/CN=${CERT_NAME}" -config "${cfg}"`,
    { stdio: "ignore" }
  );
  // -T pre-authorizes codesign to use the key without a GUI prompt.
  execSync(`security import "${key}" -k "${KEYCHAIN}" -T /usr/bin/codesign`, {
    stdio: "ignore",
  });
  execSync(`security import "${cert}" -k "${KEYCHAIN}"`, { stdio: "ignore" });
  try {
    execSync(
      `security set-key-partition-list -S apple-tool:,apple: -s -k "${keychainPassword()}" "${KEYCHAIN}"`,
      { stdio: "ignore" }
    );
  } catch {}
  try {
    // User-domain trust; if it fails headless, the signature still applies
    // and the TCC identity remains stable.
    execSync(`security add-trusted-cert -p codeSign "${cert}"`, { stdio: "ignore" });
  } catch (error) {
    console.log("add-trusted-cert skipped:", String(error).slice(0, 120));
  }
  fs.rmSync(dir, { recursive: true, force: true });
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  try {
    ensureSigningCertificate();
  } catch (error) {
    console.log("Signing certificate unavailable, using ad-hoc:", String(error).slice(0, 160));
    return;
  }
  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  console.log(`Codesign (${CERT_NAME}): ${appPath}`);
  try {
    execSync(`codesign --force --deep --sign "${CERT_NAME}" "${appPath}"`, {
      stdio: "inherit",
    });
  } catch (error) {
    console.log("Identity signing failed; falling back to ad-hoc");
    execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: "inherit" });
  }
  execSync(`codesign --verify --deep --strict "${appPath}"`, { stdio: "inherit" });
  console.log("Codesign ok");
};
