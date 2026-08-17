const { spawn } = require("child_process");
const fs = require("fs");
const log = (m) => fs.appendFileSync("C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\bridge_test5.log", m + "\n");
log("START " + new Date().toISOString());

const bridge = spawn("python", ["-u", "C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\echo.py"], {});
let out = "";
bridge.stdout.on("data", (d) => {
  out += d.toString();
  let idx;
  while ((idx = out.indexOf("\n")) >= 0) {
    const line = out.slice(0, idx).trim();
    out = out.slice(idx + 1);
    if (line) log("REPLY: " + line);
  }
});
bridge.stderr.on("data", (d) => log("STDERR: " + d.toString().trim()));
bridge.on("error", (e) => log("SPAWN ERROR: " + e.message));
setTimeout(() => {
  log("sending...");
  bridge.stdin.write("hello\n");
}, 3000);
setTimeout(() => {
  bridge.kill();
  log("killed");
}, 8000);