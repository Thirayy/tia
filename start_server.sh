
#!/bin/bash

cd /home/tia.khwarizmi.co.id/tia-server || exit 1

exec /home/tia.khwarizmi.co.id/tia-server/venv/bin/python \
  -m uvicorn main:app \
  --host 127.0.0.1 \
  --port 8000
