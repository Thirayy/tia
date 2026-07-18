# TIA VPS Deployment

Panduan ini mengasumsikan:

- Backend FastAPI berjalan di `127.0.0.1:8000`.
- Frontend Next.js berjalan di `127.0.0.1:3002`.
- Domain publik diarahkan Nginx ke frontend.
- Frontend meneruskan request `/api/*` ke backend lewat `BACKEND_URL`.

## 1. Backend

```bash
cd /path/to/tia
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Set environment production:

```bash
export DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/tia_db'
export FRONTEND_URL='https://tia.khwarizmi.co.id'
export COOKIE_DOMAIN='tia.khwarizmi.co.id'
export COOKIE_SECURE='true'
```

Smoke test backend:

```bash
venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
curl -i http://127.0.0.1:8000/docs
```

## 2. Frontend

```bash
cd /path/to/tia-app
npm ci
BACKEND_URL='http://127.0.0.1:8000' NEXT_PUBLIC_API_URL='' npm run build
npm run start -- --hostname 127.0.0.1 --port 3002
```

Smoke test frontend:

```bash
curl -i http://127.0.0.1:3002/login
curl -i http://127.0.0.1:3002/api/docs
```

## 3. PM2

Jalankan PM2 dari folder backend, sambil memberi tahu lokasi frontend:

```bash
cd /path/to/tia
TIA_BACKEND_DIR=/path/to/tia \
TIA_FRONTEND_DIR=/path/to/tia-app \
DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/tia_db' \
FRONTEND_URL='https://tia.khwarizmi.co.id' \
COOKIE_DOMAIN='tia.khwarizmi.co.id' \
BACKEND_URL='http://127.0.0.1:8000' \
pm2 start ecosystem.config.js

pm2 save
pm2 status
pm2 logs backend-tia --lines 100
pm2 logs frontend-tia --lines 100
```

Kalau `frontend-tia` mati, biasanya `.next` belum ada. Jalankan `npm run build` di folder frontend dulu.

Kalau `backend-tia` mati, cek `DATABASE_URL`, `venv`, dan `pm2 logs backend-tia`.

## 4. Nginx

Minimal server block:

```nginx
server {
    server_name tia.khwarizmi.co.id;

    location / {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 5. Login Checklist

```bash
curl -i https://tia.khwarizmi.co.id/login
curl -i https://tia.khwarizmi.co.id/api/docs
```

Di browser, setelah login sukses, response `/api/auth/login` harus mengirim cookie `session_user`.

Untuk HTTPS production:

- `COOKIE_DOMAIN=tia.khwarizmi.co.id`
- `COOKIE_SECURE=true`
- `FRONTEND_URL=https://tia.khwarizmi.co.id`

Untuk test lokal tanpa HTTPS:

- kosongkan `COOKIE_DOMAIN`
- `COOKIE_SECURE=false`
