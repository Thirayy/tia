const path = require("path");

const backendDir = process.env.TIA_BACKEND_DIR || __dirname;
const frontendDir = process.env.TIA_FRONTEND_DIR || path.resolve(__dirname, "../tia-app");
const frontendUrl = process.env.FRONTEND_URL || "https://tia.khwarizmi.co.id";
const cookieDomain = process.env.COOKIE_DOMAIN || "tia.khwarizmi.co.id";
const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";

module.exports = {
  apps: [
    {
      name: "backend-tia",
      cwd: backendDir,
      script: path.join(backendDir, "venv/bin/python"),
      args: "-m uvicorn main:app --host 127.0.0.1 --port 8000",
      interpreter: "none",
      autorestart: true,
      max_memory_restart: "300M",
      exp_backoff_restart_delay: 1000,
      env: {
        NODE_ENV: "production",
        FRONTEND_URL: frontendUrl,
        COOKIE_DOMAIN: cookieDomain,
        COOKIE_SECURE: "true",
      }
    },
    {
      name: "frontend-tia",
      cwd: frontendDir,
      script: "npm",
      args: "run start -- --hostname 127.0.0.1 --port 3002",
      interpreter: "none",
      autorestart: true,
      max_memory_restart: "500M",
      exp_backoff_restart_delay: 1000,
      env: {
        NODE_ENV: "production",
        BACKEND_URL: backendUrl,
        NEXT_PUBLIC_API_URL: "",
      },
    }
  ]
};
