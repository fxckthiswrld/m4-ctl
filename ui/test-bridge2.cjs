const { spawn } = require("child_process");
const fs = require("fs");
const log = (m) => fs.appendFileSync("C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\bridge_test2.log", m + "\n");
log("START " + new Date().toISOString());

const root = "C:\\Users\\munwig\\senhapp";

function run(label, opts) {
  return new Promise((resolve) => {
    const bridge = spawn("uv", ["run", "python", "bridge.py"], Object.assign({ cwd: root }, opts));
    let out = "";
    bridge.stdout.on("data", (d) => {
      out += d.toString();
      let idx;
      while ((idx = out.indexOf("\n")) >= 0) {
        const line = out.slice(0, idx).trim();
        out = out.slice(idx + 1);
        if (line) log(label + " REPLY: " + line);
      }
    });
    bridge.stderr.on("data", (d) => log(label + " STDERR: " + d.toString().trim()));
    bridge.on("error", (e) => log(label + " SPAWN ERROR: " + e.message));
    setTimeout(() => {
      log(label + " sending...");
      bridge.stdin.write(JSON.stringify({ cmd: "list" }) + "\n");
    }, 3000);
    setTimeout(() => {
      bridge.kill();
      log(label + " killed");
      resolve();
    }, 8000);
  });
}

(async () => {
  await run("NOSHELL", {});
  await run("SHELL", { shell: true });
  log("DONE");
})();