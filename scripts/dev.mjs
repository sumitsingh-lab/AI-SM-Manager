import { spawn } from "node:child_process";

const isWindows = process.platform === "win32";
const npx = isWindows ? "npx.cmd" : "npx";

const child = spawn(
  npx,
  ["concurrently", "-k", "-n", "client,server", "-c", "cyan,green", "npm:dev:client", "npm:dev:server"],
  { stdio: "inherit", shell: isWindows }
);

child.on("exit", (code) => process.exit(code ?? 0));
