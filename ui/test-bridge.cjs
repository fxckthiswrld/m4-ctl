const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const log = (m) => fs.appendFileSync("C:\\Users\\munwig\\AppData\\Local\\Temp\\opencode\\bridge_test.log", m + "\n");
log("START " + new Date().toISOString());

const root = "C:\\Users\\munwig\\senhapp";
const bridge = spawn("uv", ["run", "python", "bridge.py"], { cwd: root, shell: true });
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

bridge.stderr.on("data", (d) => {
  log("STDERR: " + d.toString().trim());
});

bridge.on("error", (e) => log("SPAWN ERROR: " + e.message));
bridge.on("exit", (c) => {
  log("EXIT: " + c);
  process.exit(0);
});

setTimeout(() => {
  log("sending list...");
  bridge.stdin.write(JSON.stringify({ cmd: "list" }) + "\n");
}, 3000);

setTimeout(() => {
  log("killing...");
  bridge.kill();
}, 10000);