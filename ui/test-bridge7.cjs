const { spawn } = require("child_process");
const fs = require("fs");
const log = (m) => fs.appendFileSync("C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\bridge_test7.log", m + "\n");
log("START " + new Date().toISOString());

const bridge = spawn("python", ["-u", "C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\echo3.py"], {});
let out = "";
bridge.stdout.on("data", (d) => {
  out += d.toString();
  let idx;
  while ((idx = out.indexOf("\n")) >= 0) {
    const line = out.slice(0, idx).trim();
    out = out.slice(idx + 1);
    if (line) log("OUT: " + line);
  }
});
bridge.stderr.on("data", (d) => log("STDERR: " + d.toString().trim()));
setTimeout(() => {
  log("sending hello...");
  bridge.stdin.write("hello\n");
}, 4000);
setTimeout(() => {
  bridge.kill();
  log("killed");
}, 10000);