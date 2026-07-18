module.exports = {
  apps: [
    {
      name: "backend-tia",
      cwd: "/home/TIA/tia-server",
      script: "venv/bin/uvicorn",
      args: "main:app --host 127.0.0.1 --port 8000",
      interpreter: "none",
      env: {
        NODE_ENV: "production",
      }
    },
    {
      name: "frontend-tia",
      cwd: "/home/TIA/web/tia.khwarizmi.co.id/public_html/tia-app",
      script: "npm",
      args: "run start -- --port 3002",
      interpreter: "none",
    }
  ]
};