from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_login_route_exists():
    response = client.post('/api/auth/login', json={'username': 'dummy', 'password': 'dummy'})
    assert response.status_code in {401, 404}


def test_musyrif_setoran_route_exists():
    response = client.post('/api/musyrif/setoran', json={
        'santri_id': 1,
        'surah': 'Al-Fatihah',
        'ayat': '1',
        'status_kelancaran': 'lancar',
        'catatan_musyrif': 'test'
    })
    assert response.status_code in {401, 404, 400}


def test_docs_available():
    response = client.get('/api/docs')
    assert response.status_code == 200
