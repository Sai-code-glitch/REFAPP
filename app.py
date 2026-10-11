"""REFAPP: QR-code inventory tracker.

Scan a product's QR code with a phone to add or remove stock. Works as a
mobile web app (open the site on your phone) and as a JSON API for a native
mobile client.
"""
import io
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class InputError(Exception):
    """Raised for bad request data; turned into a 400 JSON response."""


def _utcnow():
    return datetime.now(timezone.utc)


def _database_url():
    """Use DATABASE_URL (Neon, Supabase, Render) when set, otherwise local SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///inventory.db"
    # Some hosts still hand out the legacy "postgres://" scheme, which SQLAlchemy rejects.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(64), unique=True, nullable=False, index=True)
    product_name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    low_stock_threshold = db.Column(db.Integer, nullable=False, default=5)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    movements = db.relationship("Movement", backref="product", cascade="all, delete-orphan", lazy="dynamic")

    @property
    def is_low(self):
        return self.stock <= self.low_stock_threshold

    def to_dict(self):
        return {
            "id": self.id,
            "sku": self.sku,
            "product_name": self.product_name,
            "stock": self.stock,
            "low_stock_threshold": self.low_stock_threshold,
            "is_low": self.is_low,
            "qr_url": f"/api/products/{self.id}/qr.png",
        }


class Movement(db.Model):
    """Every stock change, so you can see what changed inventory and when."""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(32), nullable=False)
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "product_id": self.product_id,
            "product_name": self.product.product_name if self.product else None,
            "delta": self.delta,
            "reason": self.reason,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Backwards-compatible alias for the original model name.
Inventory = Product


def _json_error(message, status):
    return jsonify({"error": message}), status


def _int_field(data, key, default=None, minimum=None):
    value = data.get(key, default)
    if value is None or value == "":
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise InputError(f"'{key}' must be a whole number")
    if minimum is not None and value < minimum:
        raise InputError(f"'{key}' must be at least {minimum}")
    return value


def require_api_key(view):
    """If API_KEY is set, write endpoints need the X-API-Key header (or ?key=)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        expected = os.environ.get("API_KEY")
        if expected:
            supplied = request.headers.get("X-API-Key") or request.args.get("key")
            if supplied != expected:
                return _json_error("Missing or wrong API key", 401)
        return view(*args, **kwargs)
    return wrapper


def find_product(code):
    """Resolve a scanned QR payload: 'REFAPP:<sku>', a SKU, or a numeric id."""
    code = str(code if code is not None else "").strip()
    if not code:
        return None
    if code.upper().startswith("REFAPP:"):
        code = code.split(":", 1)[1].strip()
    product = Product.query.filter_by(sku=code).first()
    if product is None and code.isdigit():
        product = db.session.get(Product, int(code))
    return product


def _next_sku():
    count = Product.query.count() + 1
    while True:
        sku = f"P{count:05d}"
        if not Product.query.filter_by(sku=sku).first():
            return sku
        count += 1


def apply_change(product, delta, reason, note=None):
    """Change stock and record it. Returns False if stock would go negative."""
    if product.stock + delta < 0:
        return False
    product.stock += delta
    db.session.add(Movement(product=product, delta=delta, reason=reason, note=(note or None)))
    return True


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    )
    if test_config:
        app.config.update(test_config)

    CORS(app)
    db.init_app(app)
    with app.app_context():
        db.create_all()

    @app.errorhandler(InputError)
    def handle_input_error(err):
        return _json_error(str(err), 400)

    # ---------------- Web pages ----------------
    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/scan")
    def scan_page():
        return render_template("scan.html")

    @app.get("/labels")
    def labels_page():
        products = Product.query.order_by(Product.product_name).all()
        return render_template("labels.html", products=products)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    # ---------------- Products ----------------
    @app.get("/api/products")
    def list_products():
        query = Product.query
        search = request.args.get("q", "").strip()
        if search:
            like = f"%{search}%"
            query = query.filter(db.or_(Product.product_name.ilike(like), Product.sku.ilike(like)))
        if request.args.get("low") in ("1", "true"):
            query = query.filter(Product.stock <= Product.low_stock_threshold)
        return jsonify([p.to_dict() for p in query.order_by(Product.product_name).all()])

    @app.post("/api/products")
    @require_api_key
    def create_product():
        data = request.get_json(silent=True) or {}
        name = str(data.get("product_name") or data.get("name") or "").strip()
        if not name:
            return _json_error("'product_name' is required", 400)
        if len(name) > 100:
            return _json_error("'product_name' must be 100 characters or fewer", 400)
        sku = str(data.get("sku") or "").strip() or _next_sku()
        if len(sku) > 64:
            return _json_error("'sku' must be 64 characters or fewer", 400)
        if Product.query.filter_by(sku=sku).first():
            return _json_error(f"SKU '{sku}' is already used", 409)
        threshold = _int_field(data, "low_stock_threshold", 5, minimum=0)
        starting = _int_field(data, "stock", 0, minimum=0)
        product = Product(sku=sku, product_name=name, stock=0, low_stock_threshold=threshold)
        db.session.add(product)
        if starting:
            apply_change(product, starting, "initial")
        db.session.commit()
        return jsonify(product.to_dict()), 201

    @app.get("/api/products/<int:product_id>")
    def get_product(product_id):
        product = db.get_or_404(Product, product_id)
        result = product.to_dict()
        recent = product.movements.order_by(Movement.created_at.desc(), Movement.id.desc()).limit(20)
        result["recent_movements"] = [m.to_dict() for m in recent]
        return jsonify(result)

    @app.patch("/api/products/<int:product_id>")
    @require_api_key
    def update_product(product_id):
        product = db.get_or_404(Product, product_id)
        data = request.get_json(silent=True) or {}
        if "product_name" in data:
            name = str(data.get("product_name") or "").strip()
            if not name:
                return _json_error("'product_name' cannot be empty", 400)
            product.product_name = name[:100]
        if "low_stock_threshold" in data:
            product.low_stock_threshold = _int_field(data, "low_stock_threshold", minimum=0)
        if "stock" in data:
            target = _int_field(data, "stock", minimum=0)
            if target != product.stock:
                apply_change(product, target - product.stock, "adjustment", data.get("note"))
        db.session.commit()
        return jsonify(product.to_dict())

    @app.delete("/api/products/<int:product_id>")
    @require_api_key
    def delete_product(product_id):
        product = db.get_or_404(Product, product_id)
        db.session.delete(product)
        db.session.commit()
        return "", 204

    @app.get("/api/products/<int:product_id>/qr.png")
    def product_qr(product_id):
        import qrcode

        product = db.get_or_404(Product, product_id)
        image = qrcode.make(f"REFAPP:{product.sku}", box_size=8, border=2)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return send_file(buffer, mimetype="image/png", download_name=f"{product.sku}.png")

    # ---------------- Scanning ----------------
    @app.post("/api/scan")
    @require_api_key
    def scan():
        data = request.get_json(silent=True) or {}
        product = find_product(data.get("code", data.get("product_id")))
        if product is None:
            return _json_error("Product not found", 404)
        action = str(data.get("action") or "in").lower()
        if action not in ("in", "out"):
            return _json_error("'action' must be 'in' or 'out'", 400)
        quantity = _int_field(data, "quantity", 1, minimum=1)
        delta = quantity if action == "in" else -quantity
        if not apply_change(product, delta, f"scan_{action}", data.get("note")):
            return _json_error(f"Only {product.stock} in stock, cannot remove {quantity}", 409)
        db.session.commit()
        return jsonify({
            "message": "Inventory updated",
            "action": action,
            "quantity": quantity,
            "product": product.to_dict(),
            "stock": product.stock,
        })

    @app.get("/api/movements")
    def list_movements():
        limit = min(_int_field(request.args, "limit", 50, minimum=1), 500)
        rows = Movement.query.order_by(Movement.created_at.desc(), Movement.id.desc()).limit(limit).all()
        return jsonify([m.to_dict() for m in rows])

    # ---------------- Original endpoints (kept for existing clients) ----------------
    @app.get("/inventory")
    def get_inventory():
        products = Product.query.order_by(Product.id).all()
        return jsonify([{"id": p.id, "product_name": p.product_name, "stock": p.stock} for p in products])

    @app.post("/scan")
    @require_api_key
    def scan_qr_legacy():
        data = request.get_json(silent=True) or {}
        product = find_product(data.get("product_id"))
        if not product:
            return _json_error("Product not found", 404)
        apply_change(product, 1, "scan_in")
        db.session.commit()
        return jsonify({"message": "Inventory updated", "stock": product.stock})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
