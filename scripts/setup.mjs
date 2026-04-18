import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const root = process.cwd();
const isWindows = process.platform === "win32";
const python = isWindows ? "py" : "python3";
const pythonPrefix = isWindows ? ["-3"] : [];
const venvPython = join(root, "server", ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
const bin = (name) => (isWindows ? `${name}.cmd` : name);

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: isWindows && command.endsWith(".cmd")
  });

  if (result.error) {
    console.error(result.error.message);
  }

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

run(bin("npm"), ["install"]);
run(bin("npm"), ["install"], join(root, "client"));

if (!existsSync(join(root, "server", ".venv"))) {
  run(python, [...pythonPrefix, "-m", "venv", ".venv"], join(root, "server"));
}

run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"], join(root, "server"));
run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], join(root, "server"));

console.log("\nSetup complete. Configure .env files, then run npm run prisma:generate.");
