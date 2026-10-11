import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402

from app import create_app, db  # noqa: E402


@pytest.fixture()
def client():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.drop_all()
        db.create_all()
    with app.test_client() as c:
        yield c


def add(client, name="Water bottle", **extra):
    res = client.post("/api/products", json={"product_name": name, **extra})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_create_product_assigns_sku_and_starting_stock(client):
    p = add(client, stock=12, low_stock_threshold=3)
    assert p["sku"] == "P00001"
    assert p["stock"] == 12
    assert p["is_low"] is False
    detail = client.get(f"/api/products/{p['id']}").get_json()
    assert detail["recent_movements"][0]["reason"] == "initial"
    assert detail["recent_movements"][0]["delta"] == 12


def test_create_product_validation(client):
    assert client.post("/api/products", json={}).status_code == 400
    assert client.post("/api/products", json={"product_name": "X", "stock": -1}).status_code == 400
    assert client.post("/api/products", json={"product_name": "X", "stock": "abc"}).status_code == 400
    add(client, "A", sku="ABC")
    assert client.post("/api/products", json={"product_name": "B", "sku": "ABC"}).status_code == 409


def test_scan_in_and_out_by_qr_payload_sku_and_id(client):
    p = add(client, sku="CUP-1", stock=2)
    r = client.post("/api/scan", json={"code": "REFAPP:CUP-1", "action": "in", "quantity": 3})
    assert r.status_code == 200 and r.get_json()["stock"] == 5
    r = client.post("/api/scan", json={"code": "CUP-1", "action": "out"})
    assert r.get_json()["stock"] == 4
    r = client.post("/api/scan", json={"code": str(p["id"]), "action": "out", "quantity": 4})
    assert r.get_json()["stock"] == 0


def test_scan_out_cannot_go_negative(client):
    add(client, sku="S1", stock=1)
    r = client.post("/api/scan", json={"code": "S1", "action": "out", "quantity": 2})
    assert r.status_code == 409
    assert client.get("/api/products").get_json()[0]["stock"] == 1


def test_scan_errors(client):
    assert client.post("/api/scan", json={"code": "nope"}).status_code == 404
    add(client, sku="S1")
    assert client.post("/api/scan", json={"code": "S1", "action": "sideways"}).status_code == 400
    assert client.post("/api/scan", json={"code": "S1", "quantity": 0}).status_code == 400


def test_low_stock_filter_and_search(client):
    add(client, "Apples", stock=2, low_stock_threshold=5)
    add(client, "Bananas", stock=50, low_stock_threshold=5)
    low = client.get("/api/products?low=1").get_json()
    assert [p["product_name"] for p in low] == ["Apples"]
    found = client.get("/api/products?q=ban").get_json()
    assert [p["product_name"] for p in found] == ["Bananas"]


def test_update_and_delete(client):
    p = add(client, stock=3)
    r = client.patch(f"/api/products/{p['id']}", json={"product_name": "Renamed", "stock": 10, "low_stock_threshold": 1})
    body = r.get_json()
    assert body["product_name"] == "Renamed" and body["stock"] == 10 and body["low_stock_threshold"] == 1
    movements = client.get("/api/movements").get_json()
    assert movements[0]["reason"] == "adjustment" and movements[0]["delta"] == 7
    assert client.delete(f"/api/products/{p['id']}").status_code == 204
    assert client.get(f"/api/products/{p['id']}").status_code == 404


def test_qr_code_png(client):
    p = add(client)
    r = client.get(f"/api/products/{p['id']}/qr.png")
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_legacy_endpoints_still_work(client):
    p = add(client, stock=1)
    r = client.post("/scan", json={"product_id": p["id"]})
    assert r.get_json() == {"message": "Inventory updated", "stock": 2}
    inv = client.get("/inventory").get_json()
    assert inv == [{"id": p["id"], "product_name": "Water bottle", "stock": 2}]


def test_api_key_protects_writes(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert client.post("/api/products", json={"product_name": "X"}).status_code == 401
    ok = client.post("/api/products", json={"product_name": "X"}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 201
    # Reads stay open.
    assert client.get("/api/products").status_code == 200


def test_pages_render(client):
    add(client, "Label me")
    for path in ("/", "/scan", "/labels"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert b"Label me" in client.get("/labels").data
