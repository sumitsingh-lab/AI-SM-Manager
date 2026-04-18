import { existsSync } from "node:fs";
import { dirname, delimiter, join } from "node:path";
import { spawn } from "node:child_process";

const root = process.cwd();
const isWindows = process.platform === "win32";
const pythonPath = join(root, "server", ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
const python = existsSync(pythonPath) ? pythonPath : isWindows ? "py" : "python3";
const pythonArgsPrefix = existsSync(pythonPath) || !isWindows ? [] : ["-3"];
const env = existsSync(pythonPath)
  ? { ...process.env, PATH: `${dirname(pythonPath)}${delimiter}${process.env.PATH ?? ""}` }
  : process.env;

const child = spawn(python, [...pythonArgsPrefix, "-m", "prisma", "studio", "--schema", "server/prisma/schema.prisma"], {
  cwd: root,
  stdio: "inherit",
  env
});

child.on("exit", (code) => process.exit(code ?? 0));
