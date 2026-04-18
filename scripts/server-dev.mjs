import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawn } from "node:child_process";

const root = process.cwd();
const isWindows = process.platform === "win32";
const pythonPath = join(root, "server", ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
const python = existsSync(pythonPath) ? pythonPath : isWindows ? "py" : "python3";
const pythonArgsPrefix = existsSync(pythonPath) || !isWindows ? [] : ["-3"];

const child = spawn(
  python,
  [...pythonArgsPrefix, "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"],
  { cwd: join(root, "server"), stdio: "inherit" }
);

child.on("exit", (code) => process.exit(code ?? 0));
