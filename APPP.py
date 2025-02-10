from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Use a Free PostgreSQL Database from Neon.tech or Supabase
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://your-user:your-password@your-free-db-host"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Inventory Model
class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    stock = db.Column(db.Integer, default=0)

@app.route("/inventory", methods=["GET"])
def get_inventory():
    inventory = Inventory.query.all()
    return jsonify([{"id": item.id, "product_name": item.product_name, "stock": item.stock} for item in inventory])

@app.route("/scan", methods=["POST"])
def scan_qr():
    data = request.json
    product_id = data.get("product_id")
    product = Inventory.query.get(product_id)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    product.stock += 1
    db.session.commit()
    return jsonify({"message": "Inventory updated", "stock": product.stock})

if __name__ == "__main__":
    app.run(debug=True)
